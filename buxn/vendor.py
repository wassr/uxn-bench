import json
import subprocess
import os
import shutil
from pathlib import Path
from .logger import get_logger

logger = get_logger(__name__)

BUXN_DIR = Path(".buxn")
VENDOR_DIR = BUXN_DIR / "vendor"
BIN_DIR = BUXN_DIR / "bin"
VENDOR_CONFIG = Path("vendor.json")

def check_requirements(requires):
    for req in requires:
        if shutil.which(req) is None:
            logger.error(f"Missing required tool: {req}")
            return False
    return True

def run(args):
    logger.info(f"Starting vendor process (storing in {BUXN_DIR}/)...")
    
    if not VENDOR_CONFIG.exists():
        logger.error(f"Vendor config not found: {VENDOR_CONFIG}")
        return
        
    try:
        with open(VENDOR_CONFIG, "r") as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Failed to parse {VENDOR_CONFIG}: {e}")
        return

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    
    for name, spec in config.items():
        if not spec.get("enabled", False):
            logger.debug(f"Skipping {name} (disabled)")
            continue
            
        logger.info(f"Processing vendor dependency: {name}")
        
        requires = spec.get("requires", [])
        if not check_requirements(requires):
            logger.error(f"Skipping {name} due to missing requirements.")
            continue
            
        repo_dir = VENDOR_DIR / name
        repo_url = spec.get("repo")
        branch = spec.get("branch")
        
        if repo_url:
            if not repo_dir.exists():
                logger.info(f"Cloning {repo_url} into {repo_dir}...")
                clone_cmd = ["git", "-c", "credential.helper=", "clone"]
                if branch:
                    clone_cmd.extend(["-b", branch])
                clone_cmd.extend([repo_url, str(repo_dir)])
                
                try:
                    subprocess.run(clone_cmd, check=True, env=env)
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to clone {name}: {e}")
                    continue
            else:
                logger.info(f"Repository {name} exists. Pulling latest...")
                try:
                    subprocess.run(["git", "-c", "credential.helper=", "pull"], cwd=str(repo_dir), check=True, env=env)
                except subprocess.CalledProcessError as e:
                    logger.error(f"Failed to update {name}: {e}")
                    continue
        else:
            repo_dir.mkdir(parents=True, exist_ok=True)

        build_steps = spec.get("build_steps", [])
        for step in build_steps:
            cmd = step.get("cmd")
            if cmd:
                logger.info(f"Building {name}: {cmd}")
                try:
                    subprocess.run(cmd, cwd=str(repo_dir), shell=True, check=True)
                except subprocess.CalledProcessError as e:
                    logger.error(f"Build step failed for {name}: {e}")
                    continue
                    
            bins = step.get("bins", {})
            for src_bin, dest_bin in bins.items():
                src_path = repo_dir / src_bin
                dest_path = BIN_DIR / dest_bin
                if src_path.exists():
                    shutil.copy2(src_path, dest_path)
                    logger.info(f"Copied {src_bin} -> {dest_path}")
                else:
                    logger.warning(f"Expected binary {src_path} not found after build.")
                    
    logger.info(f"Vendor process complete. Binaries are in {BIN_DIR}/")
