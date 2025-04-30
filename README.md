# Container-Monitoring-Tool

This tool monitors all `execve` system calls made by processes inside Docker containers, enabling real-time logging of most terminal commands executed within them. It automatically detects new containers, attaches using `strace`, and forwards detailed execution logs — including command, PID, UID, timestamp, and container name — to an Elasticsearch instance for analysis and visualization in Kibana.

Features  
- Monitors all `execve` system calls (i.e., most shell and program executions) inside containers  
- Automatically detects interactive shells like `sh`, `bash`, and `ssh`  
- Captures PID, UID, timestamp, container name, and executed command  
- Sends data to Elasticsearch (`http://<VM-IP>:9200/container_logs`), viewable in Kibana (`http://<VM-IP>:5601`)

This tool is intended strictly for **ethical purposes**, such as:  
- Security auditing of systems you own  
- Academic research  
- Debugging containerized applications  

-  **Do not** use this tool to monitor systems or users without **explicit permission**. Unauthorized use may be illegal and unethical.

## 🔧 Requirements
- Docker (with Python `docker` SDK)  
- Python packages: `pytz`, `requests`  
- `strace` and `stdbuf` installed on the host  
- Elasticsearch and Kibana installed (can be inside a VM)

> ⚠️ Important:  
> - This tool must be run from the **host machine** with access to the Docker engine.  
> - It only monitors containers that are **created or started after** the script is launched.  
> - If you are running Elasticsearch and Kibana inside a VM, set `network.host: 0.0.0.0` in their config files to allow access from your host machine.  
>   Otherwise, for stricter isolation, set `network.host: 127.0.0.1` to restrict access to within the VM.

## ▶️ Run
```bash
python3 container_monitoring_tool.py
