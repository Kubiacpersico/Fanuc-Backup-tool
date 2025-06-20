# =====================================
# FANUC Robot Backup Tool (Clean Version)
# Author: Chase Kubiac
# =====================================

import os
import sys
import json
import time
import platform
import subprocess
import re
from datetime import datetime
from ftplib import FTP
from threading import Thread
from functools import wraps
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from colorama import init, Fore, Style

init(autoreset=True)

CONFIG_FILE = "job_configs.json"
HEADLESS = "--headless" in sys.argv

def print_header():
    print(Style.BRIGHT + Fore.CYAN + "\n" + " FANUC ROBOT BACKUP TOOL ".center(60))
    print(Fore.YELLOW + "  Use 'HELP' for instructions, 'CONFIG' to update settings")
    print(Fore.CYAN + "=" * 60 + "\n")

def load_configs():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_configs(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_online(ip):
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return False
    cmd = ["ping", "-n" if platform.system() == "Windows" else "-c", "1", ip]
    return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0

def validate_ip_list(ips_str):
    parsed_ips = []
    for ip_part in ips_str.split():
        full_ip = ip_part if "." in ip_part else f"192.168.1.{ip_part}"
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", full_ip):
            parsed_ips.append(full_ip)
    return parsed_ips

def get_input(prompt):
    val = input(prompt).strip()
    if val.lower() == "exit":
        print(Fore.CYAN + "\n[↩] Exiting to main menu.\n")
        main()
        sys.exit()
    return val

def ask_config(job, configs):
    print(Fore.YELLOW + "\n[CONFIG] No saved configuration for this job.")
    while True:
        folder = get_input("Enter backup folder path: ")
        if os.path.isdir(folder):
            break
        print(Fore.RED + "[✖] Folder doesn't exist.\n")

    while True:
        ips_input = get_input("Enter robot IPs (last octets or full IPs): ")
        ips = validate_ip_list(ips_input)
        nums = get_input("Enter robot numbers (e.g., 1 2 3): ").split()
        if len(ips) != len(nums):
            print(Fore.RED + "[✖] Count mismatch between IPs and robot numbers.\n")
        else:
            break

    while True:
        t = get_input("Backup type [1=MD / 2=AOA]: ")
        if t in ("1", "2"):
            btype = "MD" if t == "1" else "AOA"
            break

    user = get_input("FTP username (leave blank for anonymous): ")
    password = get_input("FTP password (leave blank for anonymous): ")

    config = {"folder": folder, "ips": ips, "nums": nums, "type": btype, "user": user, "pass": password}
    configs[job] = config
    save_configs(configs)
    return config

def edit_configs(configs):
    jobs = [j for j in configs.keys() if isinstance(configs[j], dict) and "folder" in configs[j]]
    if not jobs:
        print(Fore.RED + "[!] No saved jobs to edit.\n")
        return

    print(Fore.CYAN + "\nSaved Jobs:")
    for i, job in enumerate(jobs, 1):
        print(f"{i}. {job}")
    print(f"{len(jobs)+1}. Return to main menu")

    sel = input("Select job number to edit/delete: ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(jobs)+1):
        print(Fore.RED + "Invalid selection.\n")
        return
    if int(sel) == len(jobs)+1:
        return

    key = jobs[int(sel) - 1]
    cfg = configs[key]

    print(Fore.YELLOW + f"\nEditing Job {key}.\n1 = Edit\n2 = Delete\n3 = Cancel")
    choice = input("Your choice: ").strip()

    if choice == "2":
        confirm = input(f"Are you sure you want to delete job {key}? [y/N]: ").lower()
        if confirm == "y":
            del configs[key]
            save_configs(configs)
            print(Fore.GREEN + f"[✓] Job {key} deleted.\n")
        return
    elif choice != "1":
        return

    print(Fore.YELLOW + "Leave field blank to keep current value.")
    folder = input(f"Backup folder [{cfg['folder']}]: ").strip()
    if folder:
        cfg['folder'] = folder

    ip_input = input(f"Robot IPs [{', '.join(cfg['ips'])}]: ").strip()
    if ip_input:
        cfg['ips'] = validate_ip_list(ip_input)

    num_input = input(f"Robot numbers [{', '.join(cfg['nums'])}]: ").strip()
    if num_input:
        cfg['nums'] = num_input.split()

    type_input = input(f"Backup type (MD/AOA) [{cfg['type']}]: ").strip().upper()
    if type_input in ["MD", "AOA"]:
        cfg['type'] = type_input

    user_input = input(f"FTP username [{cfg.get('user','')}]: ").strip()
    pass_input = input(f"FTP password [{cfg.get('pass','')}]: ").strip()
    cfg['user'] = user_input if user_input else cfg.get('user','')
    cfg['pass'] = pass_input if pass_input else cfg.get('pass','')

    save_configs(configs)
    print(Fore.GREEN + "[✓] Config updated.\n")

def ftp_backup(ip, rnum, dest_folder, btype, task_id, progress, summary, user, password):
    r_path = os.path.join(dest_folder, f"R{rnum}")
    tried_once = False

    if not is_online(ip):
        progress.stop_task(task_id)
        summary.append({"robot": f"R{rnum}", "status": "Failed", "error": "Offline or unreachable"})
        return

    while True:
        files_downloaded = 0
        try:
            ftp = FTP(ip, timeout=30)
            ftp.login(user=user or '', passwd=password or '')
            ftp.cwd("mdb:" if btype == 'AOA' else "md:")
            files = [f for f in ftp.nlst() if not f.startswith(".")]

            os.makedirs(r_path, exist_ok=True)
            progress.update(task_id, total=len(files))

            for name in files:
                with open(os.path.join(r_path, name), "wb") as f:
                    ftp.retrbinary("RETR " + name, f.write)
                files_downloaded += 1
                progress.update(task_id, advance=1)

            ftp.quit()
            summary.append({"robot": f"R{rnum}", "status": "Success"})
            return

        except Exception as e:
            progress.stop_task(task_id)
            if files_downloaded > 0:
                retry = "y" if HEADLESS else input(f"R{rnum} dropped during backup. Retry? [y/N]: ").lower()
                if retry == "y":
                    try:
                        for f in os.listdir(r_path):
                            os.remove(os.path.join(r_path, f))
                        os.rmdir(r_path)
                    except: pass
                    continue
                else:
                    summary.append({"robot": f"R{rnum}", "status": "Partial", "error": "Connection dropped"})
                    return
            elif not tried_once:
                tried_once = True
                continue
            else:
                summary.append({"robot": f"R{rnum}", "status": "Failed", "error": str(e)[:60]})
                return

def main():
    print_header()
    configs = load_configs()
    choice = input("[?] Enter Job Number or Command: ").strip().lower()

    if choice == "help":
        print(Fore.YELLOW + """
FANUC BACKUP TOOL - FULL HELP
=============================

▶ WHAT THIS DOES
This tool backs up MD or AOA data from one or more FANUC robots using FTP.

▶ BASIC USAGE
1. Type a job number (e.g., 1, 2, etc.) to begin backup
2. If the job is new, you will be prompted to:
   - Choose a folder to save the backup
   - Enter robot IPs or last octets (e.g., 20 → 192.168.1.20)
   - Assign robot numbers (R1, R2, etc.)
   - Choose backup type (MD or AOA)
   - Enter FTP login (optional)

▶ MENU COMMANDS
- CONFIG → View or edit existing job configs
- HELP → View this help message
- EXIT → Quit the program

▶ FEATURES
- Automatically creates timestamped job folders
- Retries partial backups if the robot drops connection
- Deletes empty folders if no robots backed up
- Allows multiple robots per job
- Accepts short-form IPs like "20" → "192.168.1.20"

▶ NOTES
- MD is typically for system files; AOA is a full all-of-above backup
- FTP must be enabled on the robot
- If anonymous login fails, try setting user/pass in CONFIG
""")
        main(); return

    if choice == "config":
        edit_configs(configs)
        main(); return

    if choice == "exit":
        print(Fore.CYAN + "\nExiting...\n")
        sys.exit()

    job = choice
    cfg = configs.get(job) or ask_config(job, configs)

    print(Fore.GREEN + f"\n[✓] Loaded config for Job {job}:")
    for i, ip in enumerate(cfg["ips"]):
        print(f"    R{cfg['nums'][i]} → {ip}")
    print(f"    Type: {cfg['type']}\n")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    job_folder = os.path.join(cfg["folder"], f"Job{job}_{timestamp}")
    suffix = 1
    while os.path.exists(job_folder):
        job_folder = f"{job_folder}_{suffix}"
        suffix += 1
    os.makedirs(job_folder)
    print(Fore.GREEN + f"[✓] Saving to: {job_folder}\n")

    summary = []
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.fields[robot]}", justify="right"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
    )
    threads = []

    with progress:
        for ip, r in zip(cfg["ips"], cfg["nums"]):
            task_id = progress.add_task("Backing up", robot=f"R{r}", total=100)
            t = Thread(target=ftp_backup, args=(ip, r, job_folder, cfg["type"], task_id, progress, summary, cfg.get("user", ""), cfg.get("pass", "")))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    print("\nBackup Summary:")
    for result in summary:
        if result["status"] == "Success":
            print(Fore.GREEN + f"{result['robot']} - {result['status']}")
        else:
            print(Fore.RED + f"{result['robot']} - {result['status']} ({result.get('error', 'Error')})")

    if all(entry['status'] != 'Success' for entry in summary):
        try:
            os.rmdir(job_folder)
            print(Fore.YELLOW + f"\n[!] Backup failed for all robots. Deleted empty folder: {job_folder}")
        except:
            pass

    print(Fore.GREEN + "\n[✓] All backups completed.\n")
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(Fore.RED + "\n\n[✖] Interrupted by user.\n")
        sys.exit(1)
