#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para extrair o máximo de informações possíveis de um dispositivo ONVIF.
Altere IP/PORT/USER/PASS abaixo conforme necessário.
"""

import json
import sys
import traceback
from onvif import ONVIFCamera
from zeep.helpers import serialize_object

# CONFIGURAÇÃO
IP = "192.168.1.68"
PORT = 8899
USERNAME = ""   # coloque usuário (se houver)
PASSWORD = ""   # coloque senha (se houver)
# endpoint path comum: /onvif/device_service
# onvif-zeep costuma vir com WSDLs internos; se não, informe o caminho para WSDLs.
WSDL_DIR = None  # None => usa WSDL internos do package, ou caminho: '/path/to/wsdl/'

OUTPUT_JSON = f'onvif_info_{IP}.json'

# Lista de chamadas a tentar (serviços + métodos)
SERVICE_METHODS = {
    "device": [
        ("GetServices", {"IncludeCapability": True}),
        ("GetDeviceInformation", {}),
        ("GetSystemDateAndTime", {}),
        ("GetCapabilities", {"Category": "All"}),
        ("GetScopes", {}),
        ("GetHostname", {}),
        ("GetNetworkInterfaces", {}),
        ("GetNetworkProtocols", {}),
        ("GetNTP", {}),
        ("GetUsers", {}),
    ],
    "media": [
        ("GetProfiles", {}),
        # GetStreamUri requires a ProfileToken arg; we'll loop profiles if available
        ("GetServiceCapabilities", {}),
    ],
    "imaging": [
        ("GetServiceCapabilities", {}),
        ("GetImagingSettings", {}),  # requires VideoSourceToken; will try if token known
    ],
    "ptz": [
        ("GetServiceCapabilities", {}),
        ("GetNodes", {}),
    ],
    "events": [
        ("GetServiceCapabilities", {}),
    ],
    # ... outros serviços podem ser adicionados
}

def safe_call(service, method_name, args=None):
    """Chama método SOAP de forma segura, sérializando resultado quando possível."""
    args = args or {}
    try:
        method = getattr(service, method_name)
    except Exception as e:
        return {"error": f"método não encontrado: {method_name}", "exc": str(e)}
    try:
        res = method(**args) if args else method()
        return {"result": serialize_obj(res)}
    except Exception as e:
        tb = traceback.format_exc()
        return {"error": str(e), "traceback": tb}

def serialize_obj(obj):
    """Tenta serializar objeto zeep ou retorná-lo se simples."""
    try:
        return serialize_object(obj)
    except Exception:
        try:
            # fallback para JSON-serializable
            return json.loads(json.dumps(obj, default=lambda o: str(o)))
        except Exception:
            return str(obj)

def main():
    summary = {"target": f"{IP}:{PORT}", "services": {}, "errors": []}
    device_service_url = f"http://{IP}:{PORT}/onvif/device_service"
    try:
        # Cria cliente ONVIF
        if WSDL_DIR:
            cam = ONVIFCamera(IP, PORT, USERNAME, PASSWORD, wsdl_dir=WSDL_DIR)
        else:
            cam = ONVIFCamera(IP, PORT, USERNAME, PASSWORD)
    except Exception as e:
        summary["errors"].append({"stage": "connect", "error": str(e), "traceback": traceback.format_exc()})
        print("Erro ao criar ONVIFCamera:", e)
        save_and_exit(summary)

    # Tenta listar serviços disponíveis (GetServices)
    try:
        dev_service = cam.devicemgmt
        gsv = safe_call(dev_service, "GetServices", {"IncludeCapability": True})
        summary["services"]["device_GetServices"] = gsv
    except Exception as e:
        summary["errors"].append({"stage": "get_services", "error": str(e), "traceback": traceback.format_exc()})

    # Itera pelas services conhecidas e tenta chamar métodos padrão
    # Nota: para serviços como media/imaging/ptz precisamos instanciar via cam.create_<service>()
    service_creators = {
        "device": lambda: cam.devicemgmt,
        "media": lambda: cam.create_media_service(),
        "imaging": lambda: cam.create_imaging_service(),
        "ptz": lambda: cam.create_ptz_service(),
        "events": lambda: cam.create_events_service(),
        "analytics": lambda: cam.create_analytics_service(),
        # adicione mais se desejar
    }

    # guarda tokens/profile info para usar em chamadas que requerem token
    context = {"profiles": [], "video_source_tokens": []}

    for svc_name, calls in SERVICE_METHODS.items():
        svc_result = {}
        if svc_name not in service_creators:
            svc_result["note"] = "serviço não implementado no script"
            summary["services"][svc_name] = svc_result
            continue

        try:
            service = service_creators[svc_name]()
        except Exception as e:
            svc_result["error"] = f"falha ao criar serviço {svc_name}: {str(e)}"
            svc_result["traceback"] = traceback.format_exc()
            summary["services"][svc_name] = svc_result
            continue

        for method, args in calls:
            # Chamadas especiais: se método precisa de tokens, tentamos preencher
            if svc_name == "media" and method == "GetProfiles":
                out = safe_call(service, method, args)
                svc_result[method] = out
                # extrair profiles
                try:
                    profiles = out.get("result") or out.get("result", [])
                    # profiles podem já estar em lista ou dict
                    if isinstance(profiles, dict) and "Profile" in profiles:
                        profiles = profiles["Profile"]
                except Exception:
                    profiles = []
                # normalize profiles list
                if isinstance(profiles, dict):
                    profiles = [profiles]
                if isinstance(profiles, list):
                    tokens = []
                    for p in profiles:
                        t = None
                        try:
                            t = p.get("token") or p.get("@token") or p.get("token", None)
                        except Exception:
                            pass
                        if t:
                            tokens.append(t)
                    context["profiles"] = tokens
                continue

            if svc_name == "imaging" and method == "GetImagingSettings":
                # requer VideoSourceToken; tentamos usar profiles -> videoSourceToken ou tentar GetVideoSources
                tokens = context.get("video_source_tokens", [])
                if not tokens:
                    # tenta extrair de perfis media: cada profile pode ter VideoSourceConfiguration / SourceToken
                    # vamos tentar obter videoSourceToken a partir do media profiles stored earlier
                    # (as informações podem estar em summary["services"]["media_GetProfiles"])
                    med = summary["services"].get("media_GetProfiles", {})
                    profiles_data = med.get("result") if med else None
                    if profiles_data:
                        try:
                            # profiles_data pode ter keys; navegar
                            p_list = profiles_data if isinstance(profiles_data, list) else (profiles_data.get("Profile") if isinstance(profiles_data, dict) else [])
                            if isinstance(p_list, dict):
                                p_list = [p_list]
                            for p in p_list:
                                vs_token = None
                                # caminhos possíveis
                                if isinstance(p, dict):
                                    # Profile -> VideoSourceConfiguration -> SourceToken
                                    try:
                                        vsc = p.get("VideoSourceConfiguration") or p.get("VideoSourceConfiguration", {})
                                        vs_token = vsc.get("SourceToken") or vsc.get("Token")
                                    except Exception:
                                        pass
                                    # ou Profile -> videoSourceConfiguration -> sourceToken
                                if vs_token:
                                    tokens.append(vs_token)
                        except Exception:
                            pass
                if tokens:
                    svc_result[method] = []
                    for t in tokens:
                        out = safe_call(service, method, {"VideoSourceToken": t})
                        svc_result[method].append({t: out})
                    summary["services"][svc_name] = svc_result
                    continue
                else:
                    # marcar que não havia token
                    svc_result[method] = {"note": "requer VideoSourceToken; não encontrado automaticamente"}
                    summary["services"][svc_name] = svc_result
                    continue

            # default call
            out = safe_call(service, method, args)
            svc_result[method] = out

        summary["services"][svc_name] = svc_result

    # Chamadas extra úteis: GetStreamUri para cada profile
    try:
        if context.get("profiles"):
            media = cam.create_media_service()
            stream_map = {}
            for token in context["profiles"]:
                try:
                    uri_resp = media.GetStreamUri({'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}, 'ProfileToken': token})
                    stream_map[token] = serialize_obj(uri_resp)
                except Exception as e:
                    stream_map[token] = {"error": str(e), "traceback": traceback.format_exc()}
            summary["stream_uris"] = stream_map
    except Exception as e:
        summary["errors"].append({"stage": "GetStreamUri", "error": str(e), "traceback": traceback.format_exc()})

    # tenta obter certificados/firmware se disponível em GetDeviceInformation result (Manufacturer/Model/FirmwareVersion/SerialNumber/HardwareId)
    try:
        gdi = summary["services"].get("device", {}).get("GetDeviceInformation", {})
        summary["device_info_summary"] = gdi
    except Exception:
        pass

    # salva em JSON
    try:
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            #json.dump(summary, f, indent=2, ensure_ascii=False)
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        print(f"Resultado salvo em {OUTPUT_JSON}")
    except Exception as e:
        print("Erro ao salvar JSON:", e)

    # imprime resumo sucinto na tela
    print(json.dumps({
        "target": summary["target"],
        "services_found": list(summary["services"].keys()),
        "errors": len(summary["errors"])
    }, indent=2, ensure_ascii=False))

def save_and_exit(summary):
    try:
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Saída parcial gravada em {OUTPUT_JSON}")
    except Exception:
        pass
    sys.exit(1)

if __name__ == "__main__":
    main()
