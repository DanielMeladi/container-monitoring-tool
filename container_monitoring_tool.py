import docker
import time
import sys
import signal
import subprocess
import requests
import json
from threading import Thread
import os
import re
import pytz
import datetime

client = docker.from_env()
processed_containers = set()
monitored_pids = set()
recently_logged_commands = {}

LOGGING_THRESHOLD_SECONDS = 1
UID_TIMEOUT_SECONDS = 2


def signal_handler(sig, frame):
    print("\nStopping container_event_listener")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def is_container_running(container_id):
    try:
        container = client.containers.get(container_id)
        return container.status == 'running'
    except docker.errors.NotFound:
        print(f"Container {container_id} no longer exists. Skipping...")
        return False
    except Exception as e:
        print(f"Error checking container status: {e}")
        return False


def get_container_details(container_id):
    try:
        top_output = subprocess.check_output(["docker", "top", container_id, "-eo", "pid,comm"]).decode("utf-8")
        processes = top_output.splitlines()[1:]
        details = []
        for process in processes:
            parts = process.split()
            pid, cmd = parts[0], parts[1]
            if cmd in ["sh", "bash", "ssh", "sshd"]:
                details.append((container_id, pid, cmd))
        return details
    except subprocess.CalledProcessError:
        print(f"Container {container_id} is no longer running. Skipping...")
        return []


def monitor_interactive_shell(container_id, container_name):
    while not is_container_running(container_id):
        print(f"Container {container_name} is not fully running yet. Retrying...")
        time.sleep(1)

    print(f"Container {container_name} is running. Monitoring interactive shell...")

    while True:
        if not is_container_running(container_id):
            print(f"Container {container_name} is no longer running. Stopping monitoring...")
            break

        container_details = get_container_details(container_id)
        if container_details:
            for detail in container_details:
                cid, pid, cmd = detail
                if pid not in monitored_pids:
                    monitored_pids.add(pid)
                    start_strace_on_pid(container_id, pid, container_name)
        time.sleep(1)


def filter_command(command, pid):
    global recently_logged_commands
    current_time = time.time()

    excluded_commands = ["groups", "dircolors", "grep", "grepconf.sh", "locale", "sed"]
    if any(command.startswith(ex_cmd) for ex_cmd in excluded_commands):
        return None

    if command.startswith("ls"):
        command = command.replace(" --color=auto", "")

    if pid in recently_logged_commands:
        last_command, last_time = recently_logged_commands[pid]
        if command == last_command and (current_time - last_time) < LOGGING_THRESHOLD_SECONDS:
            return None

    recently_logged_commands[pid] = (command, current_time)
    return command


def start_strace_on_pid(container_id, pid, container_name):
    print(f"Starting strace on PID: {pid} for container {container_name}. Sending to Elasticsearch.")

    strace_cmd = f"sudo stdbuf -oL strace -f -p {pid} -s99999 -ff -e trace=execve 2>&1"
    process = subprocess.Popen(strace_cmd, shell=True, executable="/bin/bash", stdout=subprocess.PIPE, text=True)

    execve_buffer = {}
    exited_flag = False

    def send_with_timeout(exec_pid):
        if exec_pid in execve_buffer:
            execve_data = execve_buffer.pop(exec_pid)
            print(f"Sending After Timeout (No UID): {execve_data}")
            send_to_elasticsearch(execve_data)


    for line in process.stdout:
        print(f"Strace Output: {line.strip()}")


        match_execve = re.match(r'\[pid (\d+)\] execve\("([^"]+)", \[(.*?)\]', line)
        if match_execve:
            exec_pid = match_execve.group(1)
            command_path = match_execve.group(2)
            command_name = os.path.basename(command_path)

            arguments_raw = match_execve.group(3)
            arguments = re.findall(r'"([^"]+)"', arguments_raw)


            if arguments and arguments[0] == command_name:
                arguments = arguments[1:]


            full_command = ' '.join([command_name] + arguments).strip()
            commands = [cmd.strip() for cmd in full_command.split(",")]


            for cmd in commands:
                execve_data = {
                    "container_id": container_id,
                    "container_name": container_name,
                    "pid": exec_pid,
                    "command": [cmd],
                    "uid": "unknown",
                    "timestamp": datetime.datetime.now(pytz.timezone("Europe/Stockholm")).isoformat()
                }
                if exec_pid in execve_buffer:
                    execve_buffer[exec_pid]["command"].append(cmd)
                else:
                    execve_buffer[exec_pid] = execve_data
                print(f"Execve Detected (Pending UID): {execve_buffer[exec_pid]}")




        match_exited = re.match(r'\[pid (\d+)\] \+\+\+ exited', line)
        if match_exited:
            exited_pid = match_exited.group(1)
            if exited_pid in execve_buffer:
                print(f"Exited Detected for PID: {exited_pid}")
                exited_flag = True


        if exited_flag:
            match_sigchld = re.match(r'.*--- SIGCHLD .*si_uid=(\d+)', line)
            if match_sigchld:
                si_uid = match_sigchld.group(1)
                exited_flag = False


                if exited_pid in execve_buffer:
                    execve_data = execve_buffer.pop(exited_pid)
                    execve_data["uid"] = si_uid


                    for cmd in execve_data["command"]:
                        data_with_uid = execve_data.copy()
                        data_with_uid["command"] = [cmd]
                        print(f"Updated Execve Data with UID: {data_with_uid}")
                        send_to_elasticsearch(data_with_uid)


def send_to_elasticsearch(data):
    try:
        response = requests.post(
            "http://localhost:9200/container_logs/_doc",
            headers={"Content-Type": "application/json"},
            data=json.dumps(data)
        )
        if response.status_code != 201:
            print(f"Failed to send to Elasticsearch: {response.status_code} - {response.text}")
        else:
            print(f"Successfully sent to Elasticsearch: {data}")
    except Exception as e:
        print(f"Error sending to Elasticsearch: {e}")


def listen_for_docker_events():
    for event in client.events(decode=True):
        if event['Type'] == 'container' and event['Action'] in ('create', 'start'):
            container_id = event['id']
            container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "Unknown")
            if container_id not in processed_containers:
                processed_containers.add(container_id)
                Thread(target=monitor_interactive_shell, args=(container_id, container_name), daemon=True).start()


if __name__ == "__main__":
    listen_for_docker_events()