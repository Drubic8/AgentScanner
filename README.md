# 🚀 AgentScanner: ASIC Miner Network Manager

**AgentScanner** is a fast, multi-threaded local network scanner designed for the discovery, monitoring, and management of ASIC mining equipment.

It utilizes a Multi-layer Discovery algorithm to detect even hung devices or ASICs in deep sleep mode (where port 4028 is disabled).

## ✨ Supported Hardware
The scanner automatically recognizes the device architecture and firmware:
* **Bitmain Antminer** (Stock: S19, S21, T21, L7, L9, Z15, etc.)
* **Elphapex** (DG-series)
* **MicroBT Whatsminer** (M30S, M50, M60)
* **Canaan AvalonMiner**
* **iPollo** & **Jasminer**
* **Custom Firmwares:** Full support for the **VNish** API.

## 🛠 Key Features
* **Smart Auto-Discovery:** Primary polling via port 4028 (CGMiner API) with a smart fallback to port 80 (Web API) to detect sleeping devices.
* **Deep Diagnostics:** Extracts hardware errors (HW ERR) and accurately identifies dead or missing hashboards.
* **Full Telemetry:** Real-time display of active pools, workers, temperatures (chip and board), fan speeds, and precise uptime.
* **Hashrate Normalization:** Automatically recalculates and normalizes hashrates across different algorithms (Scrypt, SHA-256, Etchash).
* **Status System:** Equipment is strictly classified into four categories: `🟢 Running`, `⚠️ Unstable/Error`, `🔴 Offline`, and `💤 Sleep`.

## 🎮 Remote Control (MDM)
Mass management of ASIC miners is available directly from the GUI data table:
* **LED Blink:** Trigger the LED to easily locate the ASIC on the rack.
* **Sleep / Resume:** Put miners into low-power sleep mode (stop mining) and wake them up.
* **Reboot:** Remotely restart devices.
*(Supported for Whatsminer API v3, Antminer Stock/VNish, Elphapex, and Jasminer).*

## 🚀 Installation & Quick Start

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Drubic8/AgentScanner.git](https://github.com/Drubic8/AgentScanner.git)
   cd AgentScanner
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Launch the GUI:**
   ```bash
   python gemini_gui.py
   ```

## ⚙️ Architecture (How it works)
To solve the problem of fragmented API endpoints across different firmwares, AgentScanner uses two-layer polling:
1. **Socket API (4028):** Instant telemetry retrieval without passwords.
2. **HTTP Fallback (80):** If the mining process is killed (e.g., the device is asleep), the scanner bypasses basic authentication (`HTTPDigestAuth` / `HTTPBasicAuth`) to download `get_miner_conf.cgi` and `stats.cgi` to accurately determine the device's status.