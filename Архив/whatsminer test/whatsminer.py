import socket
import hashlib
import base64
import json
import time
import struct

from whatsminer_trans import *
from whatsminer_interface import *

miner_ip = "10.10.202.234"
miner_port = 4433
miner_account = "super"
miner_passwd = "super"
miner_salt = ''

whatsminer_api = WhatsminerAPIv3(miner_account, miner_passwd)
whatsminer_tcp = WhatsminerTCP(miner_ip, miner_port, miner_account, miner_passwd)
whatsminer_tcp.connect();

req_info = whatsminer_api.get_request_cmds("get.device.info");
req_length = len(req_info)
rsp_info = whatsminer_tcp.send(req_info, req_length)
if rsp_info['code'] == 0:
    miner_salt = rsp_info['msg']['salt']
    whatsminer_api.set_salt(miner_salt)
    print(f"{rsp_info}")
else:
    print(f"invalid msg {rsp_info}")
    exit()

req_info = whatsminer_api.set_miner_service("restart");
req_length = len(req_info)
rsp_info = whatsminer_tcp.send(req_info, req_length)
print(f"{rsp_info}")

# req_info = whatsminer_api.set_user_passwd("user1", "user1","abcde1");
# req_length = len(req_info)
# rsp_info = whatsminer_tcp.send(req_info, req_length)
# print(f"{rsp_info}")
