import hashlib
import base64
import json
import time
from Crypto.Cipher import AES
from passlib.hash import md5_crypt

class WhatsminerAPIv3:
    def __init__(self, account, password):
        '''
        api account only support : super, user1, user2, user3
        '''
        self.account = account
        self.password = password

    def set_salt(self, salt):
        '''
        salt should not be empty
        '''
        self.salt = salt

    def _generate_token(self, command, ts):
        salt=self.salt
        src_buff = f"{command}{self.password}{salt}{ts}"
        aes_key = hashlib.sha256(src_buff.encode('utf-8')).digest()
        dst_buff = base64.b64encode(aes_key).decode('utf-8')
        token = dst_buff[:8]
        # print(f"SHA256 Data: {aes_key.hex()}")
        # print(f"Base64 Encoded Token: {token}")
        return token

    def _encrypt_param(self, param,command,ts):
        """Encrypt the 'param' using AES-256 encryption"""
        param_str = json.dumps(param)
        src_buff = f"{command}{self.password}{self.salt}{ts}"
        aes_key = hashlib.sha256(src_buff.encode('utf-8')).digest()
        pad_len = 16 - (len(param_str) % 16)
        padded_param = param_str + (chr(pad_len) * pad_len)
        cipher = AES.new(aes_key, AES.MODE_ECB)
        encrypted_bytes = cipher.encrypt(padded_param.encode())
        encrypted_b64 = base64.b64encode(encrypted_bytes).decode()
        return encrypted_b64

    def get_request_cmds(self, cmd, param=None):
        payload = {
            "cmd": cmd,
            "param": param
        }

        print(payload)
        message = json.dumps(payload)
        return message

    def set_request_cmds(self, cmd, param=None):
        payload = {
            "cmd": cmd,
            "param": param
        }
        ts = int(time.time())
        token = self._generate_token(cmd, ts)
        payload["ts"] = ts
        payload["token"] = token
        payload["account"] = self.account
        print(payload)
        message = json.dumps(payload)
        return message

    def set_fan_poweroff_cool(self, param):
        return self.set_request_cmds('set.fan.poweroff_cool',param)

    def set_fan_temp_offset(self, param):
        return self.set_request_cmds('set.fan.temp_offset',param)        

    def set_fan_zero_speed(self, param):
        return self.set_request_cmds('set.fan.zero_speed',param)                

    def set_log_upload(self, server_ip, server_port):
        '''
        the param 'server_port' is string as "9990"
        '''
        payload = {
            "ip": server_ip,
            "port": server_port+'',
            "proto": 'udp'
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.log.upload',req_param)       

    def set_miner_cointype(self, cointype):
        payload = {
            "cointype": cointype,
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.miner.cointype',req_param)                       

    def set_miner_fastboot(self, param):
        '''
        the param is : 'enable' or 'disable'
        '''
        return self.set_request_cmds('set.miner.fastboot',param)    

    def set_miner_heat_mode(self, param):
        '''
        the param is : 'heating' / 'normal' / 'anti-icing'
        '''
        return self.set_request_cmds('set.miner.heat_mode',param)       

    def set_miner_pools(self, pool_url1, work_user1,work_passwd1,pool_url2, work_user2,work_passwd2,pool_url3, work_user3,work_passwd3):
        command = 'set.miner.pools'
        paramload = [{
                "pool": pool_url1,
                "worker": work_user1,
                "passwd": work_passwd1,
            },
            {
                "pool": pool_url2,
                "worker": work_user2,
                "passwd": work_passwd2,
            },
            {
                "pool": pool_url3,
                "worker": work_user3,
                "passwd": work_passwd3,
            }]
        payload = {
            "cmd": command,
        }
        ts = int(time.time())
        token = self._generate_token(command, ts)
        payload["ts"] = ts
        payload["token"] = token
        payload["account"] = self.account
        encryptPara = self._encrypt_param(json.dumps(paramload),command,ts);
        payload["param"] = encryptPara
        # print(payload)
        message = json.dumps(payload)
        return message     

    def set_miner_power(self, param):
        return self.set_request_cmds('set.miner.power',param)    

    def set_miner_power_percent(self, mode, percent):
        payload = {
            "percent": percent+'',
            "mode": mode
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.miner.power_percent',req_param)      

    def set_miner_power_limit(self, param):
        return self.set_request_cmds('set.miner.power_limit',param)    

    def set_miner_power_mode(self, param):
        '''
        the param is : low/normal/high
        '''
        return self.set_request_cmds('set.miner.power_mode',param)         

    def set_miner_report(self, gap):
        payload = {
            "gap": gap
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.miner.report',req_param)       

    def set_miner_restore_setting(self):
        return self.set_request_cmds('set.miner.restore_setting')   

    def set_miner_service(self, param):
        return self.set_request_cmds('set.miner.service', param)
        pass

    def set_miner_target_freq(self, param):
        return self.set_request_cmds('set.miner.target_freq', param)        

    def set_miner_upfreq_speed(self, param):
        return self.set_request_cmds('set.miner.upfreq_speed', param)           

    def set_system_hostname(self, hostname):
        payload = {
            "hostname": hostname
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.system.hostname',req_param)             

    def set_system_factory_reset(self):
        return self.set_request_cmds('set.system.factory_reset')     

    def set_system_reboot(self):
        return self.set_request_cmds('set.system.reboot')      

    def set_system_timezone(self, timezone, zonename):
        payload = {
            "timezone": timezone,
            "zonename": zonename
        }
        req_param = json.dumps(payload)
        return self.set_request_cmds('set.system.timezone',req_param)   

    def set_user_passwd(self, username, old_passwd,new_passwd):
        cmd = 'set.user.change_passwd'
        paramload = {
                "account": username,
                "new": new_passwd,
                "old": old_passwd
            }
        payload = {
            "cmd": cmd,
        }
        ts = int(time.time())
        token = self._generate_token(cmd, ts)
        payload["ts"] = ts
        payload["token"] = token
        payload["account"] = self.account
        encryptPara = self._encrypt_param(json.dumps(paramload),cmd,ts);
        payload["param"] = encryptPara

        print(payload)
        message = json.dumps(payload)
        return message                                                        