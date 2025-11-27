#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pydeploy.py — Universal Environment Synchronizer (v5.0.0)
Ultimate Edition - Production Ready

Функции:
1. Scan & Map: Умное сканирование импортов и маппинг имен пакетов
2. Sync State: Установка нужного, обновление старого, УДАЛЕНИЕ ЛИШНЕГО
3. Lock: Создание воспроизводимого requirements.lock
4. Verify: Проверка целостности окружения после установки
5. Binary Force: Поддержка сложных случаев (Python 3.13, Windows)
6. Prune: Умное удаление неиспользуемых зависимостей
7. Backup/Restore: Резервное копирование состояния окружения

Запуск:
    python pydeploy.py          -> Синхронизация (Install/Uninstall)
    python pydeploy.py update   -> Принудительное обновление всего до Fresh
    python pydeploy.py verify   -> Проверка целостности (pip check)
    python pydeploy.py prune    -> Удаление неиспользуемых пакетов
    python pydeploy.py backup   -> Резервное копирование окружения
    python pydeploy.py restore  -> Восстановление из резервной копии
"""

import sys
import subprocess
import shutil
import ast
import logging
import time
import urllib.request
import platform
import sysconfig
import os
import json
import hashlib
from pathlib import Path
from typing import List, Set, Dict, Tuple, Optional
from datetime import datetime

VERSION = "5.0.0"
VENV_NAME = ".venv"
LOCK_FILE = "requirements.lock"
REQ_FILE = "requirements.txt"
LOG_FILE = "deploy.log"
BACKUP_DIR = ".pydeploy_backups"
CACHE_FILE = ".pydeploy_cache.json"
REMOTE_MAPPING_URL = "https://raw.githubusercontent.com/bndr/pipreqs/master/pipreqs/mapping"

# Hardcoded mapping (Самые частые ошибки)
KNOWN_MAPPING = {
    "mdbx": "libmdbx", "cv2": "opencv-python", "skimage": "scikit-image",
    "PIL": "Pillow", "yaml": "PyYAML", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "sklearn": "scikit-learn", "google": "google-cloud",
    "telegram": "python-telegram-bot", "mysqldb": "mysqlclient",
    "fitz": "pymupdf", "docx": "python-docx", "discord": "discord.py",
    "dateutil": "python-dateutil", "dns": "dnspython", "redis": "redis",
    "psycopg2": "psycopg2-binary", "magic": "python-magic"
}

# ANSI Color Codes для красивого вывода
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    
    @staticmethod
    def disable_on_windows():
        """Отключить цвета на старых Windows терминалах"""
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except:
                for attr in dir(Colors):
                    if not attr.startswith('_') and attr != 'disable_on_windows':
                        setattr(Colors, attr, '')

Colors.disable_on_windows()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("pydeploy")

# Определение пути к Python в venv
if sys.platform == "win32":
    VENV_PYTHON = Path(VENV_NAME) / "Scripts" / "python.exe"
    VENV_PIP = Path(VENV_NAME) / "Scripts" / "pip.exe"
else:
    VENV_PYTHON = Path(VENV_NAME) / "bin" / "python"
    VENV_PIP = Path(VENV_NAME) / "bin" / "pip"

# Импорт packages_distributions (с fallback)
try:
    from importlib.metadata import packages_distributions, distributions
except ImportError:
    def packages_distributions():
        return {}
    def distributions():
        return []


def run(cmd: List[str], cwd: Optional[Path] = None, capture: bool = True, 
        timeout: int = 300) -> subprocess.CompletedProcess:
    """Выполнение команды с обработкой ошибок"""
    try:
        return subprocess.run(
            cmd, 
            cwd=cwd or Path.cwd(), 
            capture_output=capture, 
            text=True, 
            encoding="utf-8", 
            errors="replace",
            timeout=timeout
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 1, "", f"Exec not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", f"Timeout: {' '.join(cmd)}")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", f"Error: {e}")


def print_banner(text: str, color: str = Colors.CYAN):
    """Красивый баннер"""
    print(f"\n{color}{'═'*70}{Colors.RESET}")
    print(f"{color}{Colors.BOLD} {text}{Colors.RESET}")
    print(f"{color}{'═'*70}{Colors.RESET}")


def print_status(symbol: str, text: str, color: str = Colors.RESET):
    """Вывод статуса с символом и цветом"""
    print(f"{color}{symbol} {text}{Colors.RESET}")


def find_uv_executable() -> str:
    """Поиск исполняемого файла uv"""
    if shutil.which("uv"):
        return shutil.which("uv")
    
    filename = "uv.exe" if sys.platform == "win32" else "uv"
    candidates = []
    
    # Добавляем различные возможные пути
    try:
        candidates.append(Path(sysconfig.get_path("scripts")))
    except:
        pass
    
    try:
        scheme = "nt_user" if os.name == 'nt' else "posix_user"
        candidates.append(Path(sysconfig.get_path("scripts", scheme=scheme)))
    except:
        pass
    
    candidates.extend([
        Path(sys.executable).parent,
        Path(sys.executable).parent / "Scripts",
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin"
    ])
    
    for path in candidates:
        if path and (path / filename).exists():
            return str(path / filename)
    
    return "uv"


def ensure_uv():
    """Установка uv, если его нет"""
    uv_path = find_uv_executable()
    if Path(uv_path).exists() and Path(uv_path).is_file():
        return
    if shutil.which("uv"):
        return
    
    print_status("📦", "Installing UV package manager...", Colors.YELLOW)
    cmd = [sys.executable, "-m", "pip", "install", "--user", "uv"]
    
    # Если мы в venv, ставим без --user
    if sys.prefix != sys.base_prefix:
        cmd = [sys.executable, "-m", "pip", "install", "uv"]
    
    result = run(cmd, capture=False)
    if result.returncode != 0:
        log.warning("Failed to install UV, will use pip as fallback")


def run_uv(args: List[str], capture: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Выполнение команды через uv"""
    uv = find_uv_executable()
    if uv == "uv" and not shutil.which("uv"):
        return subprocess.CompletedProcess(args, 1, "", "UV missing")
    return run([uv] + args, capture=capture, timeout=timeout)


def create_venv() -> bool:
    """Создание виртуального окружения"""
    if VENV_PYTHON.exists():
        print_status("✓", f"Virtual environment exists: {VENV_NAME}", Colors.GREEN)
        return True
    
    print_banner("Creating Virtual Environment", Colors.BLUE)
    ensure_uv()
    
    # Попытка через uv (быстрее)
    result = run_uv(["venv", VENV_NAME], capture=False)
    if result.returncode == 0:
        print_status("✓", "Virtual environment created with UV", Colors.GREEN)
        return True
    
    # Fallback на стандартный venv
    print_status("⚠", "UV failed, falling back to standard venv...", Colors.YELLOW)
    result = run([sys.executable, "-m", "venv", VENV_NAME], capture=False)
    
    if result.returncode == 0:
        print_status("✓", "Virtual environment created", Colors.GREEN)
        return True
    
    print_status("✗", "Failed to create virtual environment", Colors.RED)
    return False


def get_stdlib() -> Set[str]:
    """Получение списка модулей стандартной библиотеки"""
    stdlib = {
        "abc", "argparse", "ast", "asyncio", "base64", "collections", "concurrent", 
        "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal", "email", 
        "enum", "functools", "glob", "gzip", "hashlib", "html", "http", "importlib", 
        "inspect", "io", "itertools", "json", "logging", "math", "multiprocessing",
        "operator", "os", "pathlib", "pickle", "platform", "pprint", "queue", "random", 
        "re", "shutil", "signal", "socket", "sqlite3", "ssl", "stat", "string", "struct", 
        "subprocess", "sys", "tempfile", "threading", "time", "tkinter", "tokenize", 
        "traceback", "types", "typing", "unittest", "urllib", "uuid", "warnings", 
        "weakref", "xml", "zipfile", "zlib", "zoneinfo", "array", "binascii", "builtins",
        "cmath", "codecs", "crypt", "curses", "dbm", "difflib", "dis", "distutils",
        "fcntl", "filecmp", "fnmatch", "formatter", "fractions", "ftplib", "getopt",
        "getpass", "gettext", "grp", "heapq", "hmac", "imaplib", "imp", "keyword",
        "linecache", "locale", "mailbox", "mailcap", "marshal", "mimetypes", "mmap",
        "modulefinder", "msilib", "msvcrt", "netrc", "nis", "nntplib", "numbers",
        "optparse", "ossaudiodev", "parser", "pdb", "pipes", "pkgutil", "poplib",
        "posix", "posixpath", "pwd", "py_compile", "pyclbr", "pydoc", "pyexpat",
        "quopri", "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
        "secrets", "select", "selectors", "shelve", "site", "smtpd", "smtplib",
        "sndhdr", "socketserver", "spwd", "statistics", "stringprep", "sunau",
        "symbol", "symtable", "syslog", "tabnanny", "tarfile", "telnetlib", "termios",
        "test", "textwrap", "this", "timeit", "token", "tomllib", "trace", "tty",
        "turtle", "turtledemo", "unicodedata", "unittest", "uu", "venv", "wave",
        "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xmlrpc", "zipapp",
        "zipimport", "_thread"
    }
    
    # Используем sys.stdlib_module_names если доступно (Python 3.10+)
    if hasattr(sys, 'stdlib_module_names'):
        stdlib.update(sys.stdlib_module_names)
    
    return stdlib


def load_cached_mapping() -> Dict[str, str]:
    """Загрузка кэшированного маппинга"""
    if not Path(CACHE_FILE).exists():
        return {}
    
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            # Проверяем срок давности кэша (7 дней)
            cache_time = cache.get('timestamp', 0)
            if time.time() - cache_time < 7 * 24 * 3600:
                return cache.get('mapping', {})
    except:
        pass
    
    return {}


def save_cached_mapping(mapping: Dict[str, str]):
    """Сохранение маппинга в кэш"""
    try:
        cache = {
            'timestamp': time.time(),
            'mapping': mapping
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        log.warning(f"Failed to save cache: {e}")


def resolve_mapping(imports: Set[str]) -> List[str]:
    """Преобразование имён модулей в имена пакетов PyPI"""
    if not imports:
        return []
    
    resolved = set()
    local_dist = packages_distributions()
    unknown = []
    
    # Сначала пробуем локальный маппинг через importlib
    for mod in imports:
        if mod in local_dist:
            dist_list = local_dist[mod]
            if dist_list:
                resolved.add(dist_list[0])
        elif mod in KNOWN_MAPPING:
            resolved.add(KNOWN_MAPPING[mod])
        else:
            unknown.append(mod)
    
    # Если есть неизвестные модули, пробуем загрузить remote mapping
    if unknown:
        cached_mapping = load_cached_mapping()
        remote_mapping = {}
        
        # Пробуем загрузить из сети
        try:
            with urllib.request.urlopen(REMOTE_MAPPING_URL, timeout=2.0) as r:
                data = r.read().decode("utf-8")
                for line in data.splitlines():
                    if ":" in line:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            remote_mapping[parts[0].strip()] = parts[1].strip()
                
                # Сохраняем в кэш
                save_cached_mapping(remote_mapping)
        except Exception as e:
            log.debug(f"Failed to fetch remote mapping: {e}")
            remote_mapping = cached_mapping
        
        # Разрешаем неизвестные модули
        for mod in unknown:
            if mod in remote_mapping:
                resolved.add(remote_mapping[mod])
            elif mod in cached_mapping:
                resolved.add(cached_mapping[mod])
            else:
                # Последняя надежда - сам модуль это и есть имя пакета
                resolved.add(mod)
                log.debug(f"Unknown module mapping: {mod} -> assuming package name")
    
    return sorted(list(resolved))


def scan_project() -> List[str]:
    """Сканирование проекта на наличие импортов"""
    print_banner("Scanning Project Imports", Colors.BLUE)
    
    imports = set()
    ignore = {VENV_NAME, "__pycache__", ".git", ".idea", ".vscode", 
              "build", "dist", ".pytest_cache", ".mypy_cache", "node_modules"}
    
    py_files = list(Path.cwd().rglob("*.py"))
    print_status("📁", f"Found {len(py_files)} Python files", Colors.CYAN)
    
    for py_file in py_files:
        # Игнорируем файлы в исключённых директориях
        if any(p in ignore for p in py_file.parts):
            continue
        
        # Игнорируем сам скрипт pydeploy
        if py_file.name == Path(__file__).name:
            continue
        
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=str(py_file))
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_name = alias.name.split('.')[0]
                        imports.add(module_name)
                
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module_name = node.module.split('.')[0]
                        imports.add(module_name)
        
        except SyntaxError:
            log.warning(f"Syntax error in {py_file}, skipping")
        except Exception as e:
            log.debug(f"Failed to parse {py_file}: {e}")
    
    # Фильтруем stdlib и private модули
    stdlib = get_stdlib()
    external = {i for i in imports if i not in stdlib and not i.startswith("_")}
    
    print_status("🔍", f"Found {len(external)} external dependencies", Colors.CYAN)
    
    # Преобразуем в имена пакетов
    packages = resolve_mapping(external)
    
    for pkg in packages:
        print_status("  •", pkg, Colors.RESET)
    
    return packages


def get_installed_packages() -> Dict[str, str]:
    """Получение установленных пакетов с версиями"""
    result = run_uv(["pip", "freeze", "--python", str(VENV_PYTHON)], capture=True)
    
    if result.returncode != 0:
        # Fallback на обычный pip
        result = run([str(VENV_PYTHON), "-m", "pip", "freeze"], capture=True)
    
    if result.returncode != 0:
        return {}
    
    packages = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        if "==" in line:
            name, version = line.split("==", 1)
            packages[name.lower()] = version
        elif "@" in line:
            # Обработка URL зависимостей (git+https://...)
            name = line.split("@")[0].strip()
            packages[name.lower()] = "url"
    
    return packages


def get_package_dependencies(package: str) -> Set[str]:
    """Получение зависимостей конкретного пакета"""
    result = run([str(VENV_PYTHON), "-m", "pip", "show", package], capture=True)
    
    if result.returncode != 0:
        return set()
    
    dependencies = set()
    for line in result.stdout.splitlines():
        if line.startswith("Requires:"):
            deps_str = line.split(":", 1)[1].strip()
            if deps_str:
                dependencies = {d.strip().lower() for d in deps_str.split(",")}
            break
    
    return dependencies


def build_dependency_tree() -> Dict[str, Set[str]]:
    """Построение дерева зависимостей всех установленных пакетов"""
    installed = get_installed_packages()
    tree = {}
    
    for package in installed.keys():
        tree[package] = get_package_dependencies(package)
    
    return tree


def find_orphaned_packages(required: List[str], installed: Dict[str, str]) -> List[str]:
    """Поиск пакетов, которые не нужны (не используются и не являются зависимостями)"""
    required_lower = {pkg.lower() for pkg in required}
    installed_lower = set(installed.keys())
    
    # Строим дерево зависимостей
    dep_tree = build_dependency_tree()
    
    # Находим все транзитивные зависимости требуемых пакетов
    needed = set(required_lower)
    to_process = list(required_lower)
    
    while to_process:
        pkg = to_process.pop()
        if pkg in dep_tree:
            for dep in dep_tree[pkg]:
                if dep not in needed and dep in installed_lower:
                    needed.add(dep)
                    to_process.append(dep)
    
    # Пакеты, которых нет ни в требуемых, ни в зависимостях - это сироты
    orphaned = installed_lower - needed
    
    # Исключаем системные пакеты (pip, setuptools, wheel, etc.)
    system_packages = {'pip', 'setuptools', 'wheel', 'distribute', 'pkg-resources', 'uv'}
    orphaned = orphaned - system_packages
    
    return sorted(list(orphaned))


def prune_orphans(desired_packages: List[str]):
    """Удаление неиспользуемых пакетов (Garbage Collection)"""
    print_banner("Pruning Unused Packages", Colors.YELLOW)
    
    installed = get_installed_packages()
    if not installed:
        print_status("ℹ", "No packages installed", Colors.CYAN)
        return
    
    orphaned = find_orphaned_packages(desired_packages, installed)
    
    if not orphaned:
        print_status("✓", "No orphaned packages found", Colors.GREEN)
        return
    
    print_status("🗑", f"Found {len(orphaned)} orphaned packages:", Colors.YELLOW)
    for pkg in orphaned:
        print_status("  •", pkg, Colors.RESET)
    
    # Запрашиваем подтверждение
    response = input(f"\n{Colors.YELLOW}Remove these packages? [y/N]: {Colors.RESET}").strip().lower()
    
    if response != 'y':
        print_status("↩", "Prune cancelled", Colors.CYAN)
        return
    
    # Удаляем по одному с обратной связью
    failed = []
    for pkg in orphaned:
        result = run([str(VENV_PYTHON), "-m", "pip", "uninstall", "-y", pkg], capture=True)
        
        if result.returncode == 0:
            print_status("✓", f"Removed: {pkg}", Colors.GREEN)
        else:
            print_status("✗", f"Failed to remove: {pkg}", Colors.RED)
            failed.append(pkg)
    
    if failed:
        print_status("⚠", f"Failed to remove {len(failed)} packages", Colors.YELLOW)
    else:
        print_status("✓", "All orphaned packages removed", Colors.GREEN)


def install_package_atomic(package: str) -> bool:
    """Установка одного пакета с различными стратегиями"""
    log.info(f"Installing: {package}")
    base_cmd = [str(VENV_PYTHON), "-m", "pip", "install", package]
    
    strategies = [
        # 1. Стандартная установка
        (base_cmd, "standard"),
        # 2. Binary only (для Windows/Python 3.13)
        (base_cmd + ["--only-binary=:all:"], "binary-only"),
        # 3. С pre-release версиями
        (base_cmd + ["--pre"], "pre-release"),
        # 4. Без зависимостей (последняя надежда)
        (base_cmd + ["--no-deps"], "no-deps"),
    ]
    
    for cmd, strategy in strategies:
        result = run(cmd, capture=True, timeout=180)
        if result.returncode == 0:
            log.debug(f"{package} installed via {strategy}")
            return True
    
    return False


def sync_dependencies(packages: List[str], force_update: bool = False):
    """Синхронизация зависимостей с lock файлом"""
    if not packages:
        print_status("ℹ", "No packages to install", Colors.CYAN)
        return
    
    ensure_uv()
    
    # 1. Генерация Lock-файла (Resolve)
    print_banner("Resolving Dependencies", Colors.BLUE)
    
    temp_req = Path("temp_requirements.in")
    temp_req.write_text("\n".join(packages), encoding="utf-8")
    
    compile_cmd = [
        "pip", "compile", 
        str(temp_req), 
        "-o", LOCK_FILE, 
        "--python", str(VENV_PYTHON),
        "--generate-hashes"  # Добавляем хеши для безопасности
    ]
    
    if force_update:
        compile_cmd.append("--upgrade")
    
    print_status("🔒", "Creating lock file with dependency resolution...", Colors.CYAN)
    res_lock = run_uv(compile_cmd, capture=False, timeout=300)
    temp_req.unlink(missing_ok=True)
    
    if res_lock.returncode != 0:
        print_status("⚠", "Lock file generation failed, using direct install", Colors.YELLOW)
        install_robust_direct(packages)
        return
    
    print_status("✓", f"Lock file created: {LOCK_FILE}", Colors.GREEN)
    
    # 2. Sync (Install from Lock)
    print_banner("Synchronizing Environment", Colors.BLUE)
    
    sync_cmd = [
        "pip", "sync", 
        LOCK_FILE, 
        "--python", str(VENV_PYTHON)
    ]
    
    print_status("📦", "Installing packages from lock file...", Colors.CYAN)
    res_sync = run_uv(sync_cmd, capture=False, timeout=600)
    
    if res_sync.returncode == 0:
        print_status("✓", "Environment synchronized successfully", Colors.GREEN)
    else:
        print_status("⚠", "UV sync failed, trying atomic fallback", Colors.YELLOW)
        install_robust_direct(packages)


def install_robust_direct(packages: List[str]):
    """Аварийная установка без lock-файла (атомарная установка каждого пакета)"""
    print_banner("ATOMIC FALLBACK INSTALL", Colors.YELLOW)
    
    failed = []
    success = []
    
    total = len(packages)
    for idx, pkg in enumerate(packages, 1):
        print(f"\n[{idx}/{total}] Processing: {pkg}")
        
        if install_package_atomic(pkg):
            print_status("✓", f"{pkg}: OK", Colors.GREEN)
            success.append(pkg)
        else:
            print_status("✗", f"{pkg}: FAILED", Colors.RED)
            failed.append(pkg)
    
    print()
    print_status("✓", f"Successfully installed: {len(success)}/{total}", Colors.GREEN)
    
    if failed:
        print_status("✗", f"Failed packages: {len(failed)}/{total}", Colors.RED)
        for pkg in failed:
            print_status("  •", pkg, Colors.RED)
        print(f"\n{Colors.YELLOW}Troubleshooting tips:{Colors.RESET}")
        print("  • Install Visual C++ Build Tools (Windows)")
        print("  • Install system dependencies (Linux: build-essential, python3-dev)")
        print("  • Try using an older Python version")
        print("  • Check if the package name is correct on PyPI")


def verify_env():
    """Проверка здоровья окружения"""
    print_banner("Environment Health Check", Colors.BLUE)
    
    checks_passed = 0
    checks_total = 4
    
    # 1. Python Runtime
    print_status("🔍", "Checking Python runtime...", Colors.CYAN)
    result = run([str(VENV_PYTHON), "--version"], capture=True)
    if result.returncode == 0:
        version = result.stdout.strip()
        print_status("✓", f"Python runtime OK: {version}", Colors.GREEN)
        checks_passed += 1
    else:
        print_status("✗", "Python runtime check failed", Colors.RED)
    
    # 2. Pip availability
    print_status("🔍", "Checking pip availability...", Colors.CYAN)
    result = run([str(VENV_PYTHON), "-m", "pip", "--version"], capture=True)
    if result.returncode == 0:
        print_status("✓", "Pip is available", Colors.GREEN)
        checks_passed += 1
    else:
        print_status("✗", "Pip check failed", Colors.RED)
    
    # 3. Dependency graph integrity (pip check)
    print_status("🔍", "Checking dependency graph...", Colors.CYAN)
    result = run([str(VENV_PYTHON), "-m", "pip", "check"], capture=True)
    if result.returncode == 0:
        print_status("✓", "Dependency graph: OK", Colors.GREEN)
        checks_passed += 1
    else:
        print_status("⚠", "Dependency issues found:", Colors.YELLOW)
        print(result.stdout)
    
    # 4. Smoke test - проверка импорта ключевых пакетов
    print_status("🔍", "Running smoke tests...", Colors.CYAN)
    
    installed = get_installed_packages()
    test_packages = [pkg for pkg in ['requests', 'numpy', 'pandas', 'flask'] 
                     if pkg in installed]
    
    if test_packages:
        smoke_passed = True
        for pkg in test_packages[:3]:  # Тестируем максимум 3 пакета
            test_code = f"import {pkg}"
            result = run([str(VENV_PYTHON), "-c", test_code], capture=True, timeout=10)
            if result.returncode != 0:
                print_status("✗", f"Failed to import {pkg}", Colors.RED)
                smoke_passed = False
        
        if smoke_passed:
            print_status("✓", "Smoke tests passed", Colors.GREEN)
            checks_passed += 1
    else:
        print_status("ℹ", "No packages to test", Colors.CYAN)
        checks_passed += 1
    
    # Итоговый результат
    print()
    if checks_passed == checks_total:
        print_status("✓", f"All checks passed ({checks_passed}/{checks_total})", Colors.GREEN)
    else:
        print_status("⚠", f"Some checks failed ({checks_passed}/{checks_total})", Colors.YELLOW)


def create_backup():
    """Создание резервной копии текущего окружения"""
    print_banner("Creating Backup", Colors.BLUE)
    
    backup_dir = Path(BACKUP_DIR)
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"backup_{timestamp}.json"
    
    installed = get_installed_packages()
    
    backup_data = {
        "timestamp": timestamp,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": installed
    }
    
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, indent=2)
    
    print_status("✓", f"Backup created: {backup_file}", Colors.GREEN)
    print_status("ℹ", f"Saved {len(installed)} packages", Colors.CYAN)


def list_backups() -> List[Path]:
    """Список доступных резервных копий"""
    backup_dir = Path(BACKUP_DIR)
    if not backup_dir.exists():
        return []
    
    return sorted(backup_dir.glob("backup_*.json"), reverse=True)


def restore_backup():
    """Восстановление окружения из резервной копии"""
    print_banner("Restore from Backup", Colors.BLUE)
    
    backups = list_backups()
    if not backups:
        print_status("ℹ", "No backups found", Colors.CYAN)
        return
    
    print("Available backups:")
    for idx, backup in enumerate(backups, 1):
        try:
            with open(backup, 'r', encoding='utf-8') as f:
                data = json.load(f)
                timestamp = data.get('timestamp', 'unknown')
                pkg_count = len(data.get('packages', {}))
                print(f"  {idx}. {timestamp} ({pkg_count} packages)")
        except:
            print(f"  {idx}. {backup.name} (corrupted)")
    
    try:
        choice = int(input(f"\n{Colors.CYAN}Select backup to restore [1-{len(backups)}]: {Colors.RESET}"))
        if not 1 <= choice <= len(backups):
            print_status("✗", "Invalid choice", Colors.RED)
            return
    except ValueError:
        print_status("✗", "Invalid input", Colors.RED)
        return
    
    backup_file = backups[choice - 1]
    
    try:
        with open(backup_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        packages = data.get('packages', {})
        if not packages:
            print_status("✗", "Backup contains no packages", Colors.RED)
            return
        
        print_status("📦", f"Restoring {len(packages)} packages...", Colors.CYAN)
        
        # Создаём временный requirements файл
        temp_req = Path("temp_restore_requirements.txt")
        requirements = [f"{name}=={version}" for name, version in packages.items()]
        temp_req.write_text("\n".join(requirements), encoding="utf-8")
        
        # Устанавливаем
        result = run([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(temp_req)], 
                    capture=False, timeout=600)
        
        temp_req.unlink(missing_ok=True)
        
        if result.returncode == 0:
            print_status("✓", "Backup restored successfully", Colors.GREEN)
        else:
            print_status("✗", "Restore failed", Colors.RED)
    
    except Exception as e:
        print_status("✗", f"Error reading backup: {e}", Colors.RED)


def show_stats():
    """Показать статистику окружения"""
    print_banner("Environment Statistics", Colors.CYAN)
    
    installed = get_installed_packages()
    
    print_status("📊", f"Total packages installed: {len(installed)}", Colors.CYAN)
    
    if Path(VENV_NAME).exists():
        venv_size = sum(f.stat().st_size for f in Path(VENV_NAME).rglob('*') if f.is_file())
        venv_size_mb = venv_size / (1024 * 1024)
        print_status("💾", f"Virtual environment size: {venv_size_mb:.1f} MB", Colors.CYAN)
    
    if Path(LOCK_FILE).exists():
        lock_time = datetime.fromtimestamp(Path(LOCK_FILE).stat().st_mtime)
        print_status("🔒", f"Lock file updated: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}", Colors.CYAN)
    
    backups = list_backups()
    print_status("💾", f"Available backups: {len(backups)}", Colors.CYAN)


def main():
    """Главная функция"""
    try:
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "sync"
        
        print(f"{Colors.BOLD}{Colors.MAGENTA}")
        print("╔═══════════════════════════════════════════════════════════════════╗")
        print(f"║  PyDeploy v{VERSION} - Universal Environment Synchronizer          ║")
        print("╚═══════════════════════════════════════════════════════════════════╝")
        print(Colors.RESET)
        print(f"{Colors.CYAN}Mode: {cmd.upper()}{Colors.RESET}\n")
        
        # Обработка команд
        if cmd == "verify":
            verify_env()
            return
        
        if cmd == "backup":
            create_backup()
            return
        
        if cmd == "restore":
            restore_backup()
            return
        
        if cmd == "stats":
            show_stats()
            return
        
        # Основной workflow: sync/update/prune
        if not create_venv():
            log.error("Virtual environment creation failed")
            sys.exit(1)
        
        # 1. Сканирование проекта
        target_packages = scan_project()
        
        if not target_packages:
            print_status("ℹ", "No external dependencies found in .py files", Colors.CYAN)
            
            if cmd == "prune":
                installed = get_installed_packages()
                if installed:
                    print_status("🗑", f"Removing all {len(installed)} packages (project has no dependencies)", Colors.YELLOW)
                    prune_orphans([])
            return
        
        # 2. Обработка команд
        if cmd == "prune":
            prune_orphans(target_packages)
        
        elif cmd in ["sync", "update"]:
            # Создаём бэкап перед синхронизацией
            installed = get_installed_packages()
            if installed:
                create_backup()
            
            # Синхронизация
            force_update = (cmd == "update")
            sync_dependencies(target_packages, force_update=force_update)
            
            # Сохраняем user-friendly requirements.txt
            Path(REQ_FILE).write_text("\n".join(target_packages), encoding="utf-8")
            print_status("✓", f"Requirements saved to {REQ_FILE}", Colors.GREEN)
            
            # Удаляем ненужные пакеты после установки
            prune_orphans(target_packages)
            
            # Проверка здоровья
            verify_env()
        
        else:
            print_status("✗", f"Unknown command: {cmd}", Colors.RED)
            print("\nAvailable commands:")
            print("  sync    - Synchronize environment with code (default)")
            print("  update  - Force update all packages to latest versions")
            print("  verify  - Check environment health")
            print("  prune   - Remove unused packages")
            print("  backup  - Create environment backup")
            print("  restore - Restore from backup")
            print("  stats   - Show environment statistics")
            sys.exit(1)
        
        # Финальное сообщение
        print()
        print(f"{Colors.BOLD}{Colors.GREEN}{'═'*70}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}🚀 Ready! Run: {VENV_PYTHON} main.py{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}{'═'*70}{Colors.RESET}")
    
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Operation cancelled by user{Colors.RESET}")
        sys.exit(0)
    
    except Exception as e:
        log.exception("Critical error occurred")
        print(f"\n{Colors.RED}{Colors.BOLD}Critical Error:{Colors.RESET} {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()