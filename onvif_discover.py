#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onvif_discover.py
Descobre automaticamente dispositivos ONVIF na rede local via WS-Discovery (UDP 3702).
Lista IPs, portas e informações básicas.
"""

import socket
import uuid
import xml.etree.ElementTree as ET
import re
import sys

# Configurações
MULTICAST_GROUP = '239.255.255.250'
PORT = 3702
TIMEOUT = 5  # segundos de espera por respostas

# Mensagem SOAP WS-Discovery (Probe)
PROBE_MESSAGE = f"""<?xml version="1.0" encoding="utf-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
  <e:Header>
    <w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>
"""

def discover_onvif_devices(timeout=TIMEOUT):
    """Envia Probe e coleta respostas WS-Discovery."""
    results = []

    # Cria socket UDP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    except Exception:
        pass

    print(f"[+] Enviando Probe ONVIF para {MULTICAST_GROUP}:{PORT} ...")
    sock.sendto(PROBE_MESSAGE.encode("utf-8"), (MULTICAST_GROUP, PORT))

    print(f"[+] Aguardando respostas por {timeout} segundos...\n")

    while True:
        try:
            data, addr = sock.recvfrom(65507)
            xml_data = data.decode(errors="ignore")
            device_info = parse_probe_response(xml_data)
            device_info["from"] = addr[0]
            results.append(device_info)
        except socket.timeout:
            break
        except Exception as e:
            print(f"[!] Erro ao processar resposta: {e}")
            continue

    sock.close()
    return results

def parse_probe_response(xml_data):
    """Extrai informações úteis de uma resposta WS-Discovery."""
    info = {"Scopes": [], "XAddrs": []}
    try:
        ns = {
            "e": "http://www.w3.org/2003/05/soap-envelope",
            "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
            "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery"
        }
        root = ET.fromstring(xml_data)

        scopes = root.findall(".//d:Scopes", ns)
        for s in scopes:
            txt = s.text or ""
            info["Scopes"].append(txt.strip())

        xaddrs = root.findall(".//d:XAddrs", ns)
        for x in xaddrs:
            info["XAddrs"].append(x.text.strip())

        # Extrair manufacturer/model/serial de Scopes se possível
        all_scopes = " ".join(info["Scopes"])
        info["Manufacturer"] = extract_scope_field(all_scopes, "onvif://www.onvif.org/name")
        info["Model"] = extract_scope_field(all_scopes, "onvif://www.onvif.org/model")
        info["Hardware"] = extract_scope_field(all_scopes, "onvif://www.onvif.org/hardware")
        info["Location"] = extract_scope_field(all_scopes, "onvif://www.onvif.org/location")
    except Exception as e:
        info["error"] = str(e)
    return info

def extract_scope_field(scope_str, key):
    """Busca valor de um campo dentro de Scopes (ex: onvif://www.onvif.org/model/ABC123)."""
    m = re.search(re.escape(key) + r"/([^ ]+)", scope_str)
    return m.group(1) if m else None

def print_results(devices):
    if not devices:
        print("[-] Nenhum dispositivo ONVIF encontrado.")
        return

    print(f"[+] {len(devices)} dispositivo(s) ONVIF encontrados:\n")
    for idx, dev in enumerate(devices, 1):
        print(f"=== Dispositivo #{idx} ===")
        print(f"IP origem       : {dev.get('from')}")
        if dev.get("XAddrs"):
            print(f"XAddr(s)        : {', '.join(dev['XAddrs'])}")
        if dev.get("Manufacturer"):
            print(f"Fabricante      : {dev['Manufacturer']}")
        if dev.get("Model"):
            print(f"Modelo          : {dev['Model']}")
        if dev.get("Hardware"):
            print(f"Hardware        : {dev['Hardware']}")
        if dev.get("Location"):
            print(f"Localização     : {dev['Location']}")
        if dev.get("Scopes"):
            print(f"Scopes          : {dev['Scopes']}")
        print()

def main():
    devices = discover_onvif_devices()
    print_results(devices)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrompido pelo usuário.")
        sys.exit(0)
