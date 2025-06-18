# ===============================
# FANUC Robot Backup Tool (CLI)
# Author: Chase Kubiac
# License: MIT

# ===============================

import os
import sys
import json
import time
from datetime import datetime
from ftplib import FTP
from threading import Thread
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, SpinnerColumn
from colorama import init, Fore, Style

# Initialize terminal color formatting
init(autoreset=True)
 
# File where job configurations are stored
CONFIG_FILE = "job_configs.json"

# ----------------------------------------
# UI: Print script header on launch
# ----------------------------------------
def print_header():
    print(Style.BRIGHT + Fore.CYAN + "\n" + " FANUC ROBOT BACKUP TOOL ".center(60))
    print(Fore.YELLOW + "  Use 'HELP' for instructions, 'CONFIG' to update settings")
    print(Fore.CYAN + "=" * 60 + "\n")

# ----------------------------------------
# Load saved job configurations from file
# ----------------------------------------
def load_configs():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

# ----------------------------------------
# Save current job configuration to file
# ----------------------------------------
def save_configs(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ----------------------------------------
# Ask user for all needed config values (if new job)
# ----------------------------------------
def ask_config(job, configs):
    print(Fore.YELLOW + "\n[CONFIG] No saved configuration for this job.")

    # Ask for folder path until valid or "exit"
    while True:
        folder = input("Enter backup folder path: ").strip()
        if folder.lower() == "exit":
            print(Fore.CYAN + "\n[↩] Returning to main menu.\n")
            main()
            return
        if os.path.isdir(folder):
            break
        print(Fore.RED + "[✖] Folder doesn't exist.\n")

    # Ask for robot IPs and numbers
    while True:
        ips = input("Enter robot IPs (last octets or full IPs): ").strip()
        if ips.lower() == "exit":
            print(Fore.CYAN + "\n[↩] Returning to main menu.\n")
            main()
            return
        ips = [ip if "." in ip else f"192.168.1.{ip}" for ip in ips.split()]
        nums = input("Enter corresponding robot numbers (e.g., 1 2 3): ").strip()
        if nums.lower() == "exit":
            print(Fore.CYAN + "\n[↩] Returning to main menu.\n")
            main()
            return
        nums = nums.split()
        if len(ips) != len(nums):
            print(Fore.RED + "[✖] Count mismatch.\n")
            continue
        break

    # Ask for backup type
    print("\nBackup type:\n1 - Full MD\n2 - AOA")
    while True:
        t = input("Choice [1/2]: ").strip()
        if t.lower() == "exit":
            print(Fore.CYAN + "\n[↩] Returning to main menu.\n")
            main()
            return
        if t in ("1", "2"):
            btype = "MD" if t == "1" else "AOA"
            break

    # Save and return new job config
    config = {"folder": folder, "ips": ips, "nums": nums, "type": btype}
    configs[job] = config
    save_configs(configs)
    return config

# ----------------------------------------
# View/edit/delete previously saved jobs
# ----------------------------------------
def edit_configs(configs):
    while True:
        if not configs:
            print(Fore.RED + "[!] No saved jobs.\n")
            return

        job_list = list(configs.keys())
        print(Fore.CYAN + "\nSaved Jobs:\n------------------------------")
        for i, job in enumerate(job_list, 1):
            print(f"{i}. Job {job}")
        print()

        selection = input("Enter job number or index to edit/delete (or type 'exit' to leave): ").strip()
        if not selection or selection.lower() == "exit":
            return
        if selection in configs:
            job_key = selection
        elif selection.isdigit() and 1 <= int(selection) <= len(job_list):
            job_key = job_list[int(selection) - 1]
        else:
            print(Fore.RED + "Invalid selection. Try again.\n")
            continue

        print(f"\n[1] Edit\n[2] Delete")
        action = input("Choose option: ").strip()
        if action == "2":
            del configs[job_key]
            save_configs(configs)
            print(Fore.GREEN + f"[✓] Job {job_key} deleted.\n")
            continue
        elif action == "1":
            c = configs[job_key]
            c["folder"] = input(f"Backup folder [{c['folder']}]: ").strip() or c["folder"]
            new_ips = input(f"Robot IPs [{', '.join(c['ips'])}]: ").strip()
            c["ips"] = [ip if "." in ip else f"192.168.1.{ip}" for ip in new_ips.split()] if new_ips else c["ips"]
            new_nums = input(f"Robot numbers [{', '.join(c['nums'])}]: ").strip().split()
            c["nums"] = new_nums if new_nums else c["nums"]
            new_type = input(f"Backup type (MD/AOA) [{c['type']}]: ").strip().upper()
            c["type"] = new_type if new_type in ["MD", "AOA"] else c["type"]
            save_configs(configs)
            print(Fore.GREEN + "[✓] Job updated.\n")
        else:
            print(Fore.RED + "Invalid action.\n")

# ----------------------------------------
# FTP connection and backup per robot thread
# ----------------------------------------
def ftp_backup(ip, rnum, dest_folder, btype, task_id, progress, summary):
    retry = 2  # Retry once on failure
    while retry:
        try:
            ftp = FTP(ip, timeout=30)
            ftp.login()
            ftp.cwd("mdb:" if btype == 'AOA' else "md:")
            files = ftp.nlst()
            files = [f for f in files if not f.startswith(".")]

            r_path = os.path.join(dest_folder, f"R{rnum}")
            os.makedirs(r_path, exist_ok=True)

            total = len(files)
            progress.update(task_id, total=total)
            # Download files via FTP
            for name in files:
                with open(os.path.join(r_path, name), "wb") as f:
                    ftp.retrbinary("RETR " + name, f.write)
                progress.update(task_id, advance=1)

            ftp.quit()
            summary.append({"robot": f"R{rnum}", "status": "Success"})
            return
        except Exception as e:
            retry -= 1
            if retry == 0:
                summary.append({"robot": f"R{rnum}", "status": "Failed"})
                with open("error_log.txt", "a") as log:
                    log.write(f"[{datetime.now()}] R{rnum} ({ip}): {str(e)}\n")
            progress.stop_task(task_id)

# ----------------------------------------
# Main loop — handles job entry and triggers backups
# ----------------------------------------
def main():
    print_header()
    configs = load_configs()

    choice = input("[?] Enter Job Number: ").strip()
    if choice.lower() == "help":
        print(Fore.YELLOW + "\nINSTRUCTIONS:")
        print("- Type a job number (e.g., 1234) to start backup.")
        print("- Type 'CONFIG' to view/edit saved jobs.")
        print("- Each job remembers its robots, IPs, type, and folder.")
        print("- You can enter IPs as '20 21 22' (auto-expands to 192.168.1.X).\n")
        sys.exit()
    elif choice.lower() == "config":
        edit_configs(configs)
        main()
        return
    elif choice.lower() == "exit":
        print(Fore.CYAN + "\nExiting...\n")
        sys.exit()

    job = choice
    cfg = configs[job] if job in configs else ask_config(job, configs)

    print(Fore.GREEN + f"\n[✓] Loaded config for Job {job}:")
    for i, ip in enumerate(cfg["ips"]):
        print(f"    R{cfg['nums'][i]} → {ip}")
    print(f"    Type: {cfg['type']}\n")

    # Create output folder based on timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    job_folder = os.path.join(cfg["folder"], f"Job{job}_{timestamp}")
    os.makedirs(job_folder, exist_ok=True)
    print(Fore.GREEN + f"[✓] Saving to: {job_folder}\n")

    summary = []  # Results per robot
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.fields[robot]}"),
        BarColumn(bar_width=25),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    )
    threads = []

    # Start parallel FTP threads per robot
    with progress:
        for ip, r in zip(cfg["ips"], cfg["nums"]):
            task_id = progress.add_task("Backing up", robot=f"R{r}", total=100)
            t = Thread(target=ftp_backup, args=(ip, r, job_folder, cfg["type"], task_id, progress, summary))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    # Print results
    print("\nBackup Summary:\n----------------")
    for result in summary:
        print(f"{result['robot']} - {result['status']}")

    print(Fore.GREEN + "\n[✓] All backups completed.\n")
    sys.exit(0)

# ----------------------------------------
# Run safely — catch CTRL+C cleanly
# ----------------------------------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(Fore.RED + "\n\n[✖] Interrupted by user.\n")
        sys.exit(1)
