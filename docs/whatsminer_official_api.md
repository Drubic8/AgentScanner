Whatsminer API documentation
Getting Started
Getting Started | Overview
This section provides an overview of how to interact with the API.

1. Connection:

The API listens for TCP connections on port 4433. A maximum of 10 concurrent clients are supported.

The heartbeat packet is 0x00,0x00,0x00,0x00. For details on data framing, see TCP Data Framing.

2. Permissions:

The API has two access levels:

Read-only access (for get.* commands) is enabled by default and requires no special configuration.

Write access (for set.* commands) is disabled by default for security. To enable it, you must:

Change the default password for the admin account, as write operations are blocked when using the default password.

Use the WhatsminerTool to enable the API write access switch.

Attempting a write operation without completing these steps will result in a "no permission" error.

3. Authentication for Write Commands:

To execute a write command (set.*), a security token is required. First, call the get.device.info command to retrieve a unique salt value. This salt is essential for generating the security token. For the detailed algorithm, see Token Generation.

4. Request Format:

The request JSON object structure depends on the command type:

Read Commands (get.*): These requests typically only require the cmd field. Some may also use the param field for specific queries.

Write Commands (set.*): These are more complex and require authentication. The object must include cmd, ts, token, account and param.

account: Specifies the operator for the command. Supported accounts are super, user1, user2, user3. For security, using the admin account is deprecated and no longer supported.

ts: A Unix timestamp used for the token generation.

token: The security token required for authentication.

5. Response Format:

The response JSON object includes the following properties: code, when, msg, and desc.

desc: Indicates which command the response corresponds to.

msg: Contains the response data, which can be a string or a JSON object.

code: Indicates the status of the request, as defined in the table below.

'code' details
| Code value | Details                   |
|------------|---------------------------|
| 0          | Success                   |
| -1         | Fail                      |
| -2         | Invalid command           |
| -3         | Invalid json item         |
| -4         | No permission             |
| -5         | Out of memory             |
Getting Started | TCP Data Framing
This section describes the data framing required for sending and receiving JSON payloads over TCP. All communication is a sequence of length-prefixed messages.

Sending a Request:

1. Generate the JSON request and encode it as an ASCII string.

2. Calculate the byte length of the JSON string.

3. Connect to the device on port 4433.

4. Send the length as a 4-byte little-endian integer.

5. Send the full JSON string.

Receiving a Response:

1. Read the first 4 bytes from the socket to determine the length of the incoming JSON response.

2. Read the specified number of bytes to receive the complete JSON response string.

The connection will be automatically closed after 300 seconds of inactivity.

Sending a Request (C Example):
Receiving a Response (C Example):

int tcpWrite(int soc_id, const cJSON *jsonData)
{
    int str_len = 0;
    int ret = -1;
    uint8_t arr_len[6];
    char *p_str = cJSON_PrintUnformatted(jsonData);

    if (!p_str) {
        return -1;
    }

    str_len = strlen(p_str);
    memcpy(arr_len, &str_len, 4);

    send(soc_id, arr_len, 4);
    ret = send(soc_id, p_str, strlen(p_str));
    if (ret != strlen(p_str)) {
        ret = -1;
    }

    free(p_str);
    return ret;
}
Getting Started | Token Generation
This section details the process for generating a security token for write (set.*) commands.

1. Call get.device.info to obtain the unique salt value for the device.

2. For each write command, you must provide the ts (timestamp), token, and account fields.

3. The token is a SHA256 hash derived from a concatenated string of command + password + salt + timestamp.

4. The resulting 32-byte SHA256 hash is then encoded in Base64.

5. The first 8 characters of the Base64 string serve as the token.

6. For commands that require encrypted parameters (e.g., set.miner.pools, set.user.change_passwd), the full 32-byte SHA256 hash is used as the AES encryption key.

7. The encrypted parameter value is then Base64 encoded and sent in the param field.

Token Generation Example:
Parameter Encryption Example (`set.miner.pools`):

unsigned char sha256_data[32];
char tmp_buff[256];
const char *str_cmd = "set.miner.pools";
const char *str_salt = "QbVy1Ou3";
const char *str_password = "abcdefg";
time_t ts = time(NULL);
int str_len = snprintf(tmp_buff, sizeof(tmp_buff), "%s%s%s%d", str_cmd, str_password, str_salt, ts);

mbedtls_sha256(( unsigned char* )tmp_buff, str_len, ( unsigned char* )sha256_data, 0);
//sha256_data is the aes key if needed.

unsigned char dst_buff[2048];
size_t olen = 0;
mbedtls_base64_encode(dst_buff, sizeof(dst_buff), &olen, sha256_data, 32);
dst_buff[8] = 0;
strcpy(str_token, ( char* )dst_buff);
// we get token info in str_token.
Device
Device | get.device.custom_data
Retrieve all previously set custom data fields for the device.

Request-Example:
{
    "cmd": "get.device.custom_data"
}
Response:
{
    "code": 0,
    "when": 1692685443,
    "msg": {
        "CustomerSn": "sn12345",
        "msg0": "msg",
        "msg1": "msg",
        "msg2": "msg",
        "msg3": "msg",
        "msg4": "msg",
        "msg5": "msg",
        "msg6": "msg",
        "msg7": "msg",
        "msg8": "msg",
        "msg9": "msg"
    },
    "desc": "get.device.custom_data"
}
Device | get.device.info
Retrieve comprehensive information about the miner's status and configuration.

The response includes details on the network, miner hardware, system firmware, power supply, and error codes.

Параметр
Название	Тип	Описание
param	String	
An optional filter to retrieve a specific section of information. Valid values include miner, system, power, network, salt, or error-code. If omitted, all sections are returned.

Request (Full Info):
Request (Filtered for Network):
{
    "cmd": "get.device.info"
}
Response:
{
    "code": 0,
    "when":	1692685443,
    "msg":	{
        "network":	{
            "ip":	"192.168.2.98",
            "proto":	"dhcp",
            "netmask":	"255.255.255.0",
            "dns":	"192.168.2.1",
            "mac":	"CA:01:14:00:04:EB",
            "gateway":	"192.168.2.1",
            "hostname":	"WhatsMiner"
        },
        "miner":	{
            "working":	"false",
            "type":	"M30S++_VH70",
            "hash-board":	"H70",
            "detect-hash-rate":	"33780:33484:34102:0",
            "cointype":	"BTC/BCH/BSV",
            "pool-strategy":	"FAILOVER",
            "heatmode":	"",
            "hash-percent":	"",
            "eeprom-liquid-cooling":	"0-0-0",
            "chipdata0":	"H35A07-22020801 BINV01-196803C",
            "chipdata1":	"H35A07-22020801 BINV01-196803C",
            "chipdata2":	"H35A07-22020801 BINV01-196803C",
            "fast-boot":	"disable",
            "board-num":	"3",
            "pcbsn0":	"HHM1EK46702216Kxxxxx",
            "pcbsn1":	"HHM1EK46702216Kxxxxx",
            "pcbsn2":	"HHM1EK46702216Kxxxxx",
            "miner-sn":	"xxxxxxxxxxxxx"
            "power-limit-set":	"3600",
            "web-pool":	1,
        },
        "system":	{
            "api":	"3.0.0",
            "platform":	"H616",
            "fwversion":	"20240722.07.51.REL",
            "control-board-version":	"CB6V10",
            "btrom":    "1",
            "apiswitch":	"1",
            "ledstatus":	"auto"
        },
        "power":	{
            "type":	"P221C",
            "mode":	"1",
            "hwversion":	"R00033",                          // Hardware version
            "swversion":	"20201224_P00031.20210326_S00037", // Software version
            "model":	"P22-12-3300-C",                       // Model name
            "iin":	14.8,                                      // Input current (A)
            "vin":	220.5,                                     // Input voltage (V)
            "vout":	1209,                                      // Output voltage (100mV unit), i.e., 12.09V
            "pin":	3264,                                      // Output power (W)
            "fanspeed":	6112,                                  // Fan speed (RPM)
            "temp0":	41.3,                                  // Temperature (°C)
            "sn":	"1355A2123xxxxxx",                         // Serial number
            "vendor":	"1"                                    // Vendor ID
        },
        "salt":	"px5hoXa9",
        "error-code":	[{
                "531":	"2025-03-12 14:52:35",
                "reason":	"Slot1 not found."
            }, {
                "532":	"2025-03-12 14:52:35",
                "reason":	"Slot2 not found."
            }]
    },
    "desc":	"get.device.info"
}
Device | set.device.custom_data
Set or update custom data fields, such as a customer-defined serial number (CustomerSn) or informational messages (msg0-msg9).

The key must be either CustomerSn or msg followed by a number from 0 to 9. The value must consist of printable characters.

Each msg field value must be less than 128 bytes in length.

Параметр
Название	Тип	Описание
param	Object	
The container for the custom data.

  key	String	
The name of the custom data field to set.

Допустимые значения: "CustomerSn", "msg0", "msg1", "msg2", "msg3", "msg4", "msg5", "msg6", "msg7", "msg8", "msg9"

  value	String	
The value to assign to the custom data field. For msg fields, the length must be less than 128 bytes.

Request-Example:
{
    "cmd": "set.device.custom_data",
    "param": {
        "key": "CustomerSn",
        "value": "CUSTSN-12345"
    },
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super"
}
Response:
{
    "code": 0,
    "when": 3521,
    "msg": "ok",
    "desc": "set.device.custom_data"
}
Fan
Fan | get.fan.setting
Retrieves a summary of the current fan settings.

Request-Example:
{
    "cmd": "get.fan.setting"
}
Response:
{
    "code": 0,
    "when": 1692858102,
    "msg": {
        "fan-poweroff-cool": 1,
        "fan-zero-speed": 0,
        "fan-temp-offset": -10
    },
    "desc": "get.fan.setting"
}
Fan | set.fan.poweroff_cool
Enables or disables the fan cooling feature that runs after the device is powered off. This is only applicable to air-cooled devices.

Параметр
Название	Тип	Описание
param	Number	
Set to 1 to enable or 0 to disable.

Допустимые значения: 0, 1

Request-Example:
{
    "cmd": "set.fan.poweroff_cool",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 1
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.fan.poweroff_cool"
}
Fan | set.fan.temp_offset
Sets the target temperature offset for the fan control system, in degrees Celsius. This feature is only available on air-cooled devices.

Параметр
Название	Тип	Описание
param	Number	
The temperature offset value. Must be a negative integer or zero.

Ограничения: -30-0

Request-Example:
{
    "cmd": "set.fan.temp_offset",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": -10
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.fan.temp_offset"
}
Fan | set.fan.zero_speed
Enables or disables the zero fan speed feature, allowing fans to stop completely under low-temperature conditions. This is only applicable to air-cooled devices.

Параметр
Название	Тип	Описание
param	Number	
Set to 1 to enable or 0 to disable.

Допустимые значения: 0, 1

Request-Example:
{
    "cmd": "set.fan.zero_speed",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 1
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.fan.zero_speed"
}
Log
Log | get.log.download
Packages the system logs into a compressed archive (.tgz) and streams it to the client over the active connection.

Download Flow:

1. The client sends the initial JSON request.

2. The server packages the logs and responds with the total log size in bytes.

3. The server then immediately begins streaming the log file in chunks of 10KB.

Request-Example:
{
    "cmd": "get.log.download"
}
Initial Response:
{
    "code": 0,
    "when": 1672281726,
    "msg": {
        "logsize": "12345"
    },
    "desc": "get.log.download"
}
Log | set.log.upload
Configures the device to stream system logs in real-time to a remote server.

This feature is equivalent to running logread -f and piping the output to a remote address.

Параметр
Название	Тип	Описание
param	Object	
Container for the log server configuration.

  ip	String	
The IP address of the remote log server.

  port	String	
The port number on the remote server.

  proto	String	
The protocol to use for log transmission (TCP or UDP).

Допустимые значения: "tcp", "udp"

Request-Example:
{
    "cmd": "set.log.upload",
    "ts": 419286050,
    "token": "8bw3WVwx",
    "account": "super",
    "param": {
        "ip": "192.168.2.22",
        "port": "4001",
        "proto": "udp"
    }
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.log.upload"
}
Miner
Miner | get.miner.history
Retrieves historical performance data for the miner within a specified time range.

The time range between begin and end cannot exceed 24 hours.

Параметр
Название	Тип	Описание
param	Object	
The container for the time range.

  begin	Number	
The start of the time range as a Unix timestamp.

  end	Number	
The end of the time range as a Unix timestamp.

Request-Example:
{
    "cmd": "get.miner.history",
    "param": {
        "begin": "1672000000",
        "end": "1672280000"
    }
}
Response:
{
    "code": 0,
    "when": 1672281726,
    "msg": "miner history information......",
    "desc": "get.miner.history"
}
Miner | get.miner.status
Retrieves real-time status information from the miner. The response can be filtered to specific sections.

Hash rate values are reported in TH/s.

Параметр
Название	Тип	Описание
param	String	
A filter to specify which information to retrieve. Multiple filters can be combined with a + separator (e.g., "summary+pools"). If omitted, no data is returned.

Допустимые значения: "pools", "summary", "edevs"

Request (Summary):
Request (Pools):
Request (Edevs):
{
    "cmd": "get.miner.status",
    "param": "summary"
}
Response (Summary):
Response (Pools):
Response (Edevs):
{
    "code": 0,
    "when": 1692685476,
    "msg": {
        "summary": {
            "elapsed": 1074,
            "bootup-time": 1175,
            "freq-avg": 788,
            "target-freq": 0,
            "factory-hash": 101.366,
            "hash-average": 101.847,
            "hash-1min": 102.072,
            "hash-15min": 101.847,
            "hash-realtime": 101.847,
            "power-rate": 31.886,
            "power-5min": 3247.641,
            "power-realtime": 3268,
            "environment-temperature": 34.8,
            "board-temperature": [
                69.6,
                70.1,
                72.3
            ],
            "chip-temp-min": 83.2,
            "chip-temp-avg": 92.9,
            "chip-temp-max": 100.3,
            "power-limit": 3500,
            "up-freq-finish": 1,
            "fan-speed-in": 4980,
            "fan-speed-out": 5070
        }
    },
    "desc": "get.miner.status"
}
Miner | set.miner.cointype
Sets the coin type for mining.

Параметр
Название	Тип	Описание
param	Object	
The container for the coin type parameter.

  cointype	String	
The coin type to set.

Допустимые значения: "BTC", "BCH", "BSV", "DCR", "HC", "DGB", "SHA256"

Request-Example:
{
    "cmd": "set.miner.cointype",
    "ts": 419300059,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "cointype": "BTC"
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.cointype"
}
Miner | set.miner.fast_hash
Enables or disables a feature to enhance hash rate during the startup phase.

This mode reduces hash rate loss during startup but may slightly increase the time to reach full stability. It is most effective on devices with fewer ASIC chips. The miner will target a power range of [power_limit - 100W, power_limit] to accelerate startup, prioritizing speed over energy efficiency.

Параметр
Название	Тип	Описание
param	Number	
Set to 1 to enable or 0 to disable.

Допустимые значения: 0, 1

Request-Example:
{
    "cmd": "set.miner.fast_hash",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 1
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.miner.fast_hash"
}
Miner | set.miner.fastboot
Enables or disables the fast boot feature.

When enabled, the device aims to reach over 85% of its target power within 10 seconds of starting. This mode has poor power efficiency and may cause instability or power overshoots. It is not recommended for typical use. The setting takes effect on the next restart of the mining service.

Параметр
Название	Тип	Описание
param	String	
The desired state for the fast boot feature.

Допустимые значения: "enable", "disable"

Request-Example:
{
    "cmd": "set.miner.fastboot",
    "ts": 419300059,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "enable"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.fastboot"
}
Miner | set.miner.heat_mode
Sets the heating mode for liquid-cooled devices.

Параметр
Название	Тип	Описание
param	String	
The desired heat mode.

Допустимые значения: "heating", "normal", "anti-freezing"

Request-Example:
{
    "cmd": "set.miner.heat_mode",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "heating"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.heat_mode"
}
Miner | set.miner.pools
Configures the mining pools for the device. Up to three pools can be set.

Important: The param field for this command must be an encrypted, Base64-encoded string. Refer to Token Generation for the encryption algorithm.

Параметр
Название	Тип	Описание
param	String	
An encrypted string containing an array of pool objects.

Unencrypted `param` Structure:
Request-Example (with encrypted `param`):
[
    {
        "pool": "stratum+tcp://pool1.example.com:3333",
        "worker": "worker_name1",
        "passwd": "password1"
    },
    {
        "pool": "stratum+tcp://pool2.example.com:4444",
        "worker": "worker_name2",
        "passwd": "password2"
    }
]
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.pools"
}
Miner | set.miner.power
Sets the miner's power consumption to a specific value in watts.

Records setting in user config. Can be set before or during runtime. If set during frequency tuning, effective next boot. If after tuning, dynamic effective.

Параметр
Название	Тип	Описание
param	Number	
The target power consumption in watts (W).

Request-Example:
{
    "cmd": "set.miner.power",
    "ts": 419300059,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 3500
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.power"
}
Miner | set.miner.power_limit
Sets the maximum power consumption for the miner in watts.

The power limit cannot exceed the device's standard operating power. The device will restart to apply the new setting.

Параметр
Название	Тип	Описание
param	Number	
The power limit in watts (W).

Ограничения: 0-99999

Request-Example:
{
    "cmd": "set.miner.power_limit",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 3000
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.miner.power_limit"
}
Miner | set.miner.power_mode
Sets the power efficiency mode for the miner.

Параметр
Название	Тип	Описание
param	String	
The desired power mode.

Допустимые значения: "low", "normal", "high"

Request-Example:
{
    "cmd": "set.miner.power_mode",
    "ts": 419300059,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "normal"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.power_mode"
}
Miner | set.miner.power_percent
Adjusts the miner's power consumption to a percentage of its initial stable power.

The adjustment is approximate and may cause instability if the target percentage is too low. The minimum stable percentage depends on the device, ambient temperature, and cooling conditions.

Only effective after frequency tuning ends, and not saved to user config file, info lost after reboot.

Параметр
Название	Тип	Описание
param	Object	
The container for the power percentage parameters.

  percent	Number	
The desired power percentage.

Ограничения: 0-100

  mode	String	
The adjustment mode.

Adjustment Modes
Fast Mode:

Speed: Adjustment completes in approximately one second.
Impact: Higher performance loss and lower stability. Recommended for temporary adjustments.
Normal Mode:

Speed: Adjustment takes several minutes.
Impact: Better stability and lower performance loss. Recommended for frequent adjustments.
Limitations: On air-cooled machines, this mode is only available in Normal or Low Power modes. Liquid-cooled machines have no restrictions.
Допустимые значения: "fast", "normal"

Request-Example:
{
    "cmd": "set.miner.power_percent",
    "ts": 419300059,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "percent": 89,
        "mode": "fast"
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.power_percent"
}
Miner | set.miner.report
Configures the device to automatically report its status at a regular interval over the current connection.

Параметр
Название	Тип	Описание
param	Object	
The container for the report interval.

  gap	Number	
The interval for status reports, in seconds. Set to 0 to disable auto-reporting. The maximum interval is 285 seconds.

Request-Example:
{
    "cmd": "set.miner.report",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "gap": 30
    }
}
Initial Response:
Auto-Report Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.report"
}
Miner | set.miner.restore_setting
Restores the miner's settings to their factory defaults.

Request-Example:
{
    "cmd": "set.miner.restore_setting",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super"
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.miner.restore_setting"
}
Miner | set.miner.service
Controls the mining service (btminer).

Параметр
Название	Тип	Описание
param	String	
The operation to perform on the service.

restart: Restarts the mining service immediately.
start: Starts or resumes the mining service.
stop: Stops or pauses the mining service.
enable: Configures the mining service to start automatically on boot.
disable: Prevents the mining service from starting on boot.
Допустимые значения: "restart", "start", "stop", "enable", "disable"

Request-Example:
{
    "cmd": "set.miner.service",
    "ts": 419318059,
    "token": "0YIjoDo/",
    "account": "super",
    "param": "restart"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.service"
}
Miner | set.miner.target_freq
Sets the target frequency as a percentage of the factory default frequency. The miner will restart to apply the new setting.

This feature is only supported on liquid-cooled devices. On air-cooled devices, it can only be used to decrease the frequency (a negative percentage).

Параметр
Название	Тип	Описание
param	Number	
The frequency adjustment percentage.

Ограничения: -100-100

Request-Example:
{
    "cmd": "set.miner.target_freq",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 78
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.miner.target_freq"
}
Miner | set.miner.upfreq_speed
Sets the speed at which the miner adjusts its frequency to reach a stable hash rate.

A higher value results in a faster startup but may increase deviation from the target hash rate and impact stability.

Параметр
Название	Тип	Описание
param	Number	
The desired speed, where 0 is normal and 10 is the fastest.

Ограничения: 0-10

Request-Example:
{
    "cmd": "set.miner.upfreq_speed",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": 7
}
Response:
{
    "code": 0,
    "when": 1692685496,
    "msg": "ok",
    "desc": "set.miner.upfreq_speed"
}
System
System | get.system.setting
Retrieves a summary of the device's current system settings.

Request-Example:
{
    "cmd": "get.system.setting"
}
Response:
{
    "code": 0,
    "when": 1692873012,
    "msg": {
        "web-pool": 1,
        "timezone": "CST-8",
        "zonename": "Asia/Shanghai",
        "hostname": "api-v2",
        "log-upload": {
            "ip": "192.168.2.133",
            "port": "9008",
            "proto": "tcp"
        },
        "time-randomized": {
            "start": 10,
            "stop": 10
        },
        "ntp-server": [
            "0.cn.pool.ntp.org",
            "0.openwrt.pool.ntp.org",
            "0.asia.pool.ntp.org",
            "0.pool.ntp.org"
        ]
    },
    "desc": "get.system.setting"
}
System | set.system.factory_reset
Resets the device to its factory default settings.

This operation will revert the following: network configuration, system passwords, user permissions, API switch (disabling it), web pool settings, saved pools, power mode, and power limit. The device will reboot upon completion.

Request-Example:
{
    "cmd": "set.system.factory_reset",
    "ts": 439304089,
    "token": "Y0Ijoro/",
    "account": "super"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.factory_reset"
}
System | set.system.hostname
Sets the device's hostname. The new configuration takes effect after a system restart.

Параметр
Название	Тип	Описание
param	Object	
The container for the hostname setting.

  hostname	String	
The new hostname for the device.

Request-Example:
{
    "cmd": "set.system.hostname",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "hostname": "WhatsMiner-Dev"
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.hostname"
}
System | set.system.led
Controls the device's LED indicators.

This command supports two modes:

auto: The system controls the LEDs automatically based on the device status.
manual: Define custom flashing patterns for the red and green LEDs.
Параметр
Название	Тип	Описание
param	String|Object[]	
Either the string "auto" or an array of objects to define flashing patterns.

  color	String	
The color of the LED to configure.

Допустимые значения: "red", "green"

  period	Number	
The duration of one full flash cycle, in milliseconds.

  duration	Number	
The duration the LED stays lit during a cycle, in milliseconds.

  start	Number	
The start offset for the flashing cycle, in milliseconds.

Request (Manual Flash Mode):
Request (Auto Mode):
{
    "cmd": "set.system.led",
    "ts": 42299611,
    "token": "NImnro3I",
    "account": "super",
    "param": [
        {
            "color": "red",
            "period": 200,
            "duration": 100,
            "start": 0
        },
        {
            "color": "green",
            "period": 200,
            "duration": 150,
            "start": 0
        }
    ]
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.led"
}
System | set.system.net_config
Configures the device's network settings. The device will reboot to apply the new configuration.

Параметр
Название	Тип	Описание
param	String|Object	
Can be the string "dhcp" to enable DHCP, or a JSON object for a static IP configuration.

  ip	String	
The static IP address.

  netmask	String	
The subnet mask.

  gateway	String	
The gateway address.

  dns	String	
The DNS server address.

Request (DHCP):
Request (Static IP):
{
    "cmd": "set.system.net_config",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "dhcp"
}
Response:
{
    "code": 0,
    "when": 1672281726,
    "msg": "ok",
    "desc": "set.system.net_config"
}
System | set.system.ntp_server
Configures the NTP (Network Time Protocol) servers for the device.

Параметр
Название	Тип	Описание
param	String	
A comma-separated list of NTP server addresses.

Request-Example:
{
    "cmd": "set.system.ntp_server",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "0.cn.pool.ntp.org,0.openwrt.pool.ntp.org"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.ntp_server"
}
System | set.system.reboot
Reboots the device. This command takes effect immediately.

Request-Example:
{
    "cmd": "set.system.reboot",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.reboot"
}
System | set.system.time_randomized
Configures a randomized delay for starting network-dependent services and stopping mining operations. The values are specified in seconds and should not exceed 120.

Параметр
Название	Тип	Описание
param	Object	
The container for the delay settings.

  start	Number	
The maximum random delay (in seconds) before starting network services.

  stop	Number	
The maximum random delay (in seconds) before stopping mining.

Request-Example:
{
    "cmd": "set.system.time_randomized",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "start": 10,
        "stop": 10
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.time_randomized"
}
System | set.system.timezone
Sets the system's time zone. This configuration does not take effect until the network is restarted.

Параметр
Название	Тип	Описание
param	Object	
The container for the time zone settings.

  timezone	String	
The time zone string (e.g., "CST-8").

  zonename	String	
The time zone name (e.g., "Asia/Shanghai").

Request-Example:
{
    "cmd": "set.system.timezone",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "timezone": "CST-8",
        "zonename": "Asia/Shanghai"
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.timezone"
}
System | set.system.update_firmware
Initiates a firmware upgrade process.

Upgrade Flow:

The client sends the initial JSON request.
The server responds with a "msg": "ready" to indicate it is prepared for the file transfer.
The client sends the firmware file, prefixed with a 4-byte little-endian integer representing the total file size.
After the transfer is complete, the server sends a final confirmation, and the device reboots to apply the update.
Request-Example:
{
    "cmd": "set.system.update_firmware",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super"
}
Initial Response:
{
    "code": 0,
    "when": 1672281726,
    "msg": "ready",
    "desc": "set.system.update_firmware"
}
System | set.system.webpools
Enables or disables the ability to configure mining pools from the web interface.

Параметр
Название	Тип	Описание
param	String	
The desired state for the web pool configuration feature.

Допустимые значения: "enable", "disable"

Request-Example:
{
    "cmd": "set.system.webpools",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": "enable"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.system.webpools"
}
User
User | set.user.change_passwd
Changes the password for a specified user account.

Important: The param field for this command must be an encrypted, Base64-encoded string. Refer to Token Generation for the encryption algorithm.

Параметр
Название	Тип	Описание
param	String	
An encrypted string containing the password change details.

Unencrypted `param` Structure:
Request-Example (with encrypted `param`):
{
    "account": "user1",
    "new": "new_password_here",
    "old": "old_password_here"
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.user.change_passwd"
}
User | set.user.permission
Sets the command permissions for a user account. This command can only be executed by an account with super privileges.

Параметр
Название	Тип	Описание
param	Object	
The container for the permission settings.

  user	String	
The user account to configure.

Допустимые значения: "user1", "user2", "user3"

  permission	String	
A comma-separated list of API commands that the user is allowed to execute (e.g., "set.system.led,set.miner.pools").

Request-Example:
{
    "cmd": "set.user.permission",
    "ts": 419304089,
    "token": "Y0Ijoro/",
    "account": "super",
    "param": {
        "user": "user1",
        "permission": "set.system.led,set.miner.pools,set.miner.service"
    }
}
Response:
{
    "code": 0,
    "when": 1692685512,
    "msg": "ok",
    "desc": "set.user.permission"
}
Сгенерировано с помощью apidoc 1.2.0 - Wed Nov 19 2025 17:07:23 GMT+0800 (China Standard Time)