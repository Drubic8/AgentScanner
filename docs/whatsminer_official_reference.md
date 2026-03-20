# Whatsminer API documentation

## Getting Started | Overview
This section provides an overview of how to interact with the API.

### 1. Connection:
The API listens for TCP connections on port **4433**. A maximum of **10 concurrent clients** are supported.
The heartbeat packet is `0x00,0x00,0x00,0x00`. For details on data framing, see TCP Data Framing.

### 2. Permissions:
The API has two access levels:
* **Read-only access** (for `get.*` commands) is enabled by default and requires no special configuration.
* **Write access** (for `set.*` commands) is disabled by default for security. To enable it, you must:
  * Change the default password for the admin account, as write operations are blocked when using the default password.
  * Use the WhatsminerTool to enable the API write access switch.

Attempting a write operation without completing these steps will result in a "no permission" error.

### 3. Authentication for Write Commands:
To execute a write command (`set.*`), a security token is required. First, call the `get.device.info` command to retrieve a unique `salt` value. This salt is essential for generating the security token. For the detailed algorithm, see Token Generation.

### 4. Request Format:
The request JSON object structure depends on the command type:
* **Read Commands (`get.*`):** These requests typically only require the `cmd` field. Some may also use the `param` field for specific queries.
* **Write Commands (`set.*`):** These are more complex and require authentication.

---

## User | set.user.change_passwd
Sets a new password for the specified user account.

**Important:** The `param` field for this command must be an encrypted, Base64-encoded string. Refer to Token Generation for the encryption algorithm.

### Параметры
| Название | Тип | Описание |
| :--- | :--- | :--- |
| `param` | String | An encrypted string containing the password change details. |

**Unencrypted `param` Structure:**
```json
{
    "account": "user1",
    "new": "new_password_here",
    "old": "old_password_here"
}

