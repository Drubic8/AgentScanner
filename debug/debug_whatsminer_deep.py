import json
import socket
import struct

IP = "192.168.122.15"
PORT = 4433

def send_cmd(ip, cmd, param=None):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, PORT))
        
        payload = {"cmd": cmd}
        if param: payload["param"] = param
            
        json_str = json.dumps(payload)
        pkg = struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
        s.sendall(pkg)
        
        len_data = s.recv(4)
        if not len_data or len(len_data) < 4: return None
        pkg_len = struct.unpack('<I', len_data)[0]
        
        chunks = []
        bytes_recd = 0
        while bytes_recd < pkg_len:
            chunk = s.recv(min(pkg_len - bytes_recd, 4096))
            if not chunk: break
            chunks.append(chunk)
            bytes_recd += len(chunk)
            
        return json.loads(b''.join(chunks).decode('utf-8', errors='ignore'))
    except Exception as e:
        return {"error": str(e)}
    finally:
        try: s.close()
        except: pass

if __name__ == "__main__":
    print(f"=== ГЛУБОКИЙ АНАЛИЗ СТАТУСА {IP} ===")
    
    print("\n1. Команда: get.device.info")
    info = send_cmd(IP, "get.device.info")
    print(json.dumps(info, indent=2))
    
    print("\n2. Команда: get.miner.status (summary)")
    summary = send_cmd(IP, "get.miner.status", "summary")
    print(json.dumps(summary, indent=2))
    
    print("\n3. Команда: get.miner.status (pools)")
    pools = send_cmd(IP, "get.miner.status", "pools")
    print(json.dumps(pools, indent=2))
    
    print("\n4. Команда: get.error_code (Спец. запрос ошибок)")
    err_cmd = send_cmd(IP, "get.error_code")
    print(json.dumps(err_cmd, indent=2))

    print("\n5. Команда: status (Старая cgminer команда)")
    cg_status = send_cmd(IP, "status")
    print(json.dumps(cg_status, indent=2))