#!/usr/bin/env python3
import os
import json
import shutil
import sys
from cai.tui.app import CONFIG_FILE, _load_tui_config, _save_tui_config


def print_ok(msg):
    print(f"[OK] {msg}")


def print_fail(msg):
    print(f"[FAIL] {msg}")


print("Running TUI config smoke tests...")
print(f"CONFIG_FILE={CONFIG_FILE}")

backup = None
if os.path.exists(CONFIG_FILE):
    backup = CONFIG_FILE + ".bak"
    shutil.copy2(CONFIG_FILE, backup)
    print(f"Backed up existing config to: {backup}")
else:
    print("No existing config file found; starting fresh.")

# Start with clean config
try:
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    print("Cleaned existing config file")
except Exception as e:
    print_fail(f"Couldn't remove existing config: {e}")

# Test: save provider
try:
    cfg = _load_tui_config()
    cfg.setdefault("providers", {})["testprov"] = "secret123"
    _save_tui_config(cfg)
    cfg2 = _load_tui_config()
    if cfg2.get("providers", {}).get("testprov") == "secret123":
        print_ok("provider save/persist")
    else:
        print_fail("provider save/persist")
except Exception as e:
    print_fail(f"provider test exception: {e}")

# Test: model params
try:
    cfg = _load_tui_config()
    cfg["model_params"] = {"temperature": 0.5, "max_tokens": 512, "system_prompt": "hello"}
    _save_tui_config(cfg)
    cfg2 = _load_tui_config()
    if cfg2.get("model_params", {}).get("temperature") == 0.5:
        print_ok("model params save/persist")
    else:
        print_fail("model params save/persist")
except Exception as e:
    print_fail(f"model params test exception: {e}")

# Test: export (write a file)
export_path = os.path.join(os.getcwd(), "tui_config_export_test.json")
try:
    cfg = _load_tui_config()
    with open(export_path, "w") as f:
        json.dump(cfg, f, indent=2)
    if os.path.exists(export_path):
        print_ok("export file created")
    else:
        print_fail("export file creation")
except Exception as e:
    print_fail(f"export test exception: {e}")

# Test: import (load a file and merge)
import_path = os.path.join(os.getcwd(), "tui_config_import_test.json")
try:
    imported = {"imported_key": "imported_value"}
    with open(import_path, "w") as f:
        json.dump(imported, f)
    with open(import_path, "r") as f:
        imported_loaded = json.load(f)
    cfg = _load_tui_config()
    cfg.update(imported_loaded)
    _save_tui_config(cfg)
    cfg2 = _load_tui_config()
    if cfg2.get("imported_key") == "imported_value":
        print_ok("import config/merge")
    else:
        print_fail("import config/merge")
except Exception as e:
    print_fail(f"import test exception: {e}")

# Test: env set/unset saved to config
env_var = "CAI_TEST_SMOKE"
try:
    os.environ[env_var] = "val1"
    cfg = _load_tui_config()
    cfg.setdefault("env", {})[env_var] = "val1"
    _save_tui_config(cfg)
    cfg2 = _load_tui_config()
    if cfg2.get("env", {}).get(env_var) == "val1":
        print_ok("env set persisted")
    else:
        print_fail("env set persisted")

    # Unset
    os.environ.pop(env_var, None)
    cfg = _load_tui_config()
    if "env" in cfg and env_var in cfg["env"]:
        cfg["env"].pop(env_var, None)
    _save_tui_config(cfg)
    cfg2 = _load_tui_config()
    if env_var not in cfg2.get("env", {}):
        print_ok("env unset persisted")
    else:
        print_fail("env unset persisted")
except Exception as e:
    print_fail(f"env set/unset test exception: {e}")

# Test: toggle session recording env var
try:
    cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
    # toggle
    if cur:
        os.environ.pop("CAI_DISABLE_SESSION_RECORDING", None)
    else:
        os.environ["CAI_DISABLE_SESSION_RECORDING"] = "true"
    print_ok("toggled CAI_DISABLE_SESSION_RECORDING (env) - manual review may be required")
    # revert
    if cur:
        os.environ["CAI_DISABLE_SESSION_RECORDING"] = "true"
    else:
        os.environ.pop("CAI_DISABLE_SESSION_RECORDING", None)
except Exception as e:
    print_fail(f"toggle session recording exception: {e}")

# Test: reset defaults (remove config file)
try:
    with open(CONFIG_FILE, "w") as f:
        json.dump({"temp": "x"}, f)
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
    if not os.path.exists(CONFIG_FILE):
        print_ok("reset defaults removed config file")
    else:
        print_fail("reset defaults removal")
except Exception as e:
    print_fail(f"reset defaults exception: {e}")

# Cleanup temp files
try:
    if os.path.exists(export_path):
        os.remove(export_path)
    if os.path.exists(import_path):
        os.remove(import_path)
except Exception:
    pass

# Restore original config if backed up
if backup:
    try:
        shutil.move(backup, CONFIG_FILE)
        print("Restored original config from backup")
    except Exception as e:
        print_fail(f"failed to restore backup: {e}")

print("TUI config smoke tests complete")
