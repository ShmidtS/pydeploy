#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pydeploy.py — Universal Environment Synchronizer (v5.1.0)
Ultimate Edition - Production Ready

Функции:
1. Scan & Map: Умное сканирование импортов и маппинг имен пакетов
2. Global First: Сначала обновляем глобальные пакеты, потом .venv
3. Local Detect: Автоопределение локальных пакетов проекта (пропускает их)
4. Conflict Install: В .venv ставим только при конфликтах
5. Lock: Создание воспроизводимого requirements.lock
6. Verify: Проверка целостности окружения после установки
7. Binary Force: Поддержка сложных случаев (Python 3.13, Windows)
8. Prune: Умное удаление неиспользуемых зависимостей
9. Backup/Restore: Резервное копирование состояния окружения

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
from typing import List, Set, Dict, Tuple, Optional, Union
from datetime import datetime

VERSION = "5.1.0"
VENV_NAME = ".venv"
LOCK_FILE = "requirements.lock"
REQ_FILE = "requirements.txt"
LOG_FILE = "deploy.log"
BACKUP_DIR = ".pydeploy_backups"
CACHE_FILE = ".pydeploy_cache.json"
LOCAL_PACKAGES_FILE = "local_packages.txt"
REMOTE_MAPPING_URL = "https://raw.githubusercontent.com/bndr/pipreqs/master/pipreqs/mapping"
CACHE_EXPIRY_DAYS = 7
MAX_PACKAGE_NAME_LENGTH = 214  # PyPI limit

# Hardcoded mapping (Самые частые ошибки)
KNOWN_MAPPING = {
    "mdbx": "libmdbx", "cv2": "opencv-python", "skimage": "scikit-image",
    "PIL": "Pillow", "yaml": "PyYAML", "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv", "sklearn": "scikit-learn",
    "telegram": "python-telegram-bot", "mysqldb": "mysqlclient",
    "fitz": "pymupdf", "docx": "python-docx", "discord": "discord.py",
    "dateutil": "python-dateutil", "dns": "dnspython",
    "psycopg2": "psycopg2-binary", "magic": "python-magic",
    "genai": "google-generativeai"
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
            except (OSError, AttributeError, ImportError):
                for attr in dir(Colors):
                    if not attr.startswith('_') and attr != 'disable_on_windows':
                        setattr(Colors, attr, '')

Colors.disable_on_windows()

# Настройка логирования (с поддержкой cp1251)
class SafeStreamHandler(logging.StreamHandler):
    """Stream handler который заменяет несовместимые символы"""
    def emit(self, record):
        try:
            msg = self.format(record)
            self.stream.write(msg + self.terminator)
            self.flush()
        except UnicodeEncodeError:
            msg = record.getMessage().encode('ascii', 'replace').decode('ascii')
            self.stream.write(msg + '\n')
            self.flush()
        except Exception:
            self.handleError(record)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        SafeStreamHandler(sys.stdout)
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

# Глобальный Python (текущий интерпретатор)
GLOBAL_PYTHON = sys.executable

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
    if not cmd:
        return subprocess.CompletedProcess(cmd, 1, "", "Empty command")

    try:
        return subprocess.run(
            cmd,
            cwd=cwd or Path.cwd(),
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False
        )
    except FileNotFoundError:
        log.error(f"Command not found: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 1, "", f"Exec not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        log.warning(f"Command timeout: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 1, "", f"Timeout: {' '.join(cmd)}")
    except (OSError, ValueError) as e:
        log.error(f"Command execution error: {e}")
        return subprocess.CompletedProcess(cmd, 1, "", f"Error: {e}")


def print_banner(text: str, color: str = Colors.CYAN) -> None:
    """Красивый баннер"""
    if not text:
        return
    print(f"\n{color}{'='*70}{Colors.RESET}")
    print(f"{color}{Colors.BOLD} {text}{Colors.RESET}")
    print(f"{color}{'='*70}{Colors.RESET}")


def print_status(symbol: str, text: str, color: str = Colors.RESET) -> None:
    """Вывод статуса с символом и цветом"""
    if not text:
        return
    try:
        print(f"{color}{symbol} {text}{Colors.RESET}")
    except UnicodeEncodeError:
        # Fallback для консолей без поддержки Unicode (cp1251 и т.д.)
        safe_symbol = symbol.encode('ascii', 'replace').decode('ascii')
        safe_text = text.encode('ascii', 'replace').decode('ascii')
        print(f"{color}{safe_symbol} {safe_text}{Colors.RESET}")


def find_uv_executable() -> str:
    """Поиск исполняемого файла uv"""
    if shutil.which("uv"):
        return shutil.which("uv")

    filename = "uv.exe" if sys.platform == "win32" else "uv"
    candidates = []

    # Добавляем различные возможные пути
    try:
        candidates.append(Path(sysconfig.get_path("scripts")))
    except (KeyError, ValueError, AttributeError):
        pass

    try:
        scheme = "nt_user" if os.name == 'nt' else "posix_user"
        candidates.append(Path(sysconfig.get_path("scripts", scheme=scheme)))
    except (KeyError, ValueError, AttributeError):
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
            # Проверяем срок давности кэша
            cache_time = cache.get('timestamp', 0)
            if time.time() - cache_time < CACHE_EXPIRY_DAYS * 24 * 3600:
                return cache.get('mapping', {})
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as e:
        log.debug(f"Failed to load cache: {e}")

    return {}


def save_cached_mapping(mapping: Dict[str, str]) -> None:
    """Сохранение маппинга в кэш"""
    if not mapping:
        return
    try:
        cache = {
            'timestamp': time.time(),
            'mapping': mapping
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except (OSError, PermissionError, json.JSONEncodeError) as e:
        log.warning(f"Failed to save cache: {e}")


def validate_package_name(name: str) -> bool:
    """Валидация имени пакета согласно PEP 508"""
    if not name or not isinstance(name, str):
        return False
    if len(name) > MAX_PACKAGE_NAME_LENGTH:
        return False
    # Базовые проверки: только буквы, цифры, дефисы, подчеркивания, точки
    if not all(c.isalnum() or c in ('-', '_', '.') for c in name):
        return False
    return True


# =============================================================================
# ОПРЕДЕЛЕНИЕ ЛОКАЛЬНЫХ ПАКЕТОВ ПРОЕКТА
# =============================================================================

def detect_local_packages() -> Set[str]:
    """
    Автоматическое определение локальных пакетов проекта.
    Пакет считается локальным если:
    1. Есть setup.py или pyproject.toml в корне проекта (это сам пакет)
    2. Есть пакеты-директории с __init__.py (namespace/local пакеты)
    3. Пользователь указал их в local_packages.txt
    """
    local = set()
    project_root = Path.cwd()
    skip_dirs = {'.venv', '.git', '.omc', '.idea', '.vscode', '__pycache__',
                 '.pytest_cache', '.mypy_cache', 'node_modules', '.memorious',
                 '.serena', 'build', 'dist', '.pydeploy_backups'}

    # 1. Проверяем наличие локального пакета (setup.py/pyproject.toml)
    has_local_package = (
        (project_root / "setup.py").exists() or
        (project_root / "pyproject.toml").exists() or
        (project_root / "setup.cfg").exists()
    )

    if has_local_package:
        # Пытаемся определить имя пакета из setup.py/pyproject.toml
        local_names = _extract_local_package_names(project_root)
        local.update(local_names)

    # 2. Рекурсивно ищем директории с __init__.py (локальные пакеты)
    for item in project_root.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name in skip_dirs:
            continue
        # Директории верхнего уровня с __init__.py — точно локальные пакеты
        if (item / "__init__.py").exists():
            local.add(item.name)
            log.debug(f"Detected local package directory: {item.name}")
        # Рекурсивно ищем подпакеты
        _detect_packages_recursive(item, local, project_root, skip_dirs)

    # 3. Ищем namespace-пакеты (директории без __init__.py, но с pyproject.toml
    #    или sub-directories с __init__.py под ними)
    _detect_namespace_packages(project_root, local, skip_dirs)

    # 4. Загружаем список локальных пакетов из файла
    local_file = project_root / LOCAL_PACKAGES_FILE
    if local_file.exists():
        try:
            for line in local_file.read_text(encoding='utf-8').splitlines():
                pkg = line.strip()
                if pkg and not pkg.startswith('#'):
                    local.add(pkg)
                    log.debug(f"Loaded local package from file: {pkg}")
        except (OSError, UnicodeDecodeError) as e:
            log.warning(f"Failed to read {LOCAL_PACKAGES_FILE}: {e}")

    return local


def _extract_local_package_names(project_root: Path) -> Set[str]:
    """Извлечение имён локальных пакетов из setup.py/pyproject.toml"""
    names = set()

    # Из setup.py
    setup_py = project_root / "setup.py"
    if setup_py.exists():
        try:
            content = setup_py.read_text(encoding='utf-8', errors='ignore')
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == 'setup':
                        for kw in node.keywords:
                            if kw.arg == 'name':
                                if isinstance(kw.value, ast.Constant):
                                    names.add(str(kw.value.value).lower().replace('-', '_').replace('.', '_'))
                                elif isinstance(kw.value, ast.Str):
                                    names.add(kw.value.s.lower().replace('-', '_').replace('.', '_'))
        except (SyntaxError, ValueError, OSError):
            pass

    # Из pyproject.toml
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding='utf-8', errors='ignore')
            for line in content.splitlines():
                line = line.strip()
                if line.startswith('name'):
                    # Простой парсинг: name = "my-package"
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        name = parts[1].strip().strip('"').strip("'")
                        names.add(name.lower().replace('-', '_').replace('.', '_'))
        except (OSError, UnicodeDecodeError):
            pass

    return names


def _detect_packages_recursive(directory: Path, local: Set[str], project_root: Path, skip_dirs: Set[str], max_depth: int = 3) -> None:
    """Рекурсивный поиск директорий с __init__.py (локальные пакеты)."""
    if max_depth <= 0:
        return

    if (directory / "__init__.py").exists():
        local.add(directory.name)
        log.debug(f"Detected local package directory: {directory.name}")

    for item in directory.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name in skip_dirs:
            continue
        _detect_packages_recursive(item, local, project_root, skip_dirs, max_depth - 1)


def _detect_namespace_packages(project_root: Path, local: Set[str], skip_dirs: Set[str]) -> None:
    """Обнаружение namespace-пакетов (директории с подпакетами-дочерними модулями)."""
    for item in project_root.iterdir():
        if not item.is_dir() or item.name.startswith('.') or item.name in skip_dirs:
            continue
        # Проверяем поддиректории: если есть дочерние пакеты, то родительская
        # директория тоже считается частью пакета (например, src/ -> src/)
        sub_packages = set()
        for sub in item.iterdir():
            if sub.is_dir() and (sub / "__init__.py").exists():
                sub_packages.add(sub.name)
        # Если нашлись дочерние пакеты, добавляем их имена как локальные
        for sp in sub_packages:
            local.add(sp)
            log.debug(f"Detected namespace sub-package: {sp}")


# =============================================================================
# ГЛОБАЛЬНЫЕ ПАКЕТЫ (СИСТЕМНЫЕ)
# =============================================================================

def get_global_packages() -> Dict[str, str]:
    """Получение установленных глобальных (системных) пакетов"""
    result = run([GLOBAL_PYTHON, "-m", "pip", "freeze"], capture=True)

    if result.returncode != 0:
        log.warning("Failed to get global packages list")
        return {}

    packages = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        try:
            if "==" in line:
                name, version = line.split("==", 1)
                packages[name.lower().strip()] = version.strip()
            elif "@" in line:
                name = line.split("@")[0].strip()
                if name:
                    packages[name.lower()] = "url"
        except ValueError:
            log.debug(f"Failed to parse global package line: {line}")
            continue

    return packages


def update_global_packages(packages: List[str], force: bool = False) -> Dict[str, str]:
    """
    Обновление глобальных пакетов.
    Возвращает словарь {пакет: статус} где статус:
      'updated' - успешно обновлён
      'installed' - установлен впервые
      'ok' - уже актуален
      'conflict' - конфликт версий (нужен .venv)
      'failed' - не удалось обновить
    """
    print_banner("Updating Global Packages", Colors.GREEN)

    global_pkgs = get_global_packages()
    results = {}
    conflicts = []

    for pkg in packages:
        pkg_name = pkg.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
        pkg_lower = pkg_name.lower()

        if pkg_lower not in global_pkgs:
            # Пакет не установлен глобально — устанавливаем
            print_status("📦", f"[global] Installing: {pkg_name}", Colors.CYAN)
            result = run([GLOBAL_PYTHON, "-m", "pip", "install", pkg], capture=True, timeout=180)
            if result.returncode == 0:
                results[pkg_lower] = 'installed'
                print_status("✓", f"[global] Installed: {pkg_name}", Colors.GREEN)
            else:
                results[pkg_lower] = 'conflict'
                conflicts.append(pkg_name)
                print_status("⚠", f"[global] Failed to install: {pkg_name} → will try in .venv", Colors.YELLOW)
        else:
            # Пакет установлен — обновляем если force
            if force:
                print_status("🔄", f"[global] Updating: {pkg_name}", Colors.CYAN)
                result = run([GLOBAL_PYTHON, "-m", "pip", "install", "--upgrade", pkg], capture=True, timeout=180)
                if result.returncode == 0:
                    results[pkg_lower] = 'updated'
                    print_status("✓", f"[global] Updated: {pkg_name}", Colors.GREEN)
                else:
                    results[pkg_lower] = 'conflict'
                    conflicts.append(pkg_name)
                    print_status("⚠", f"[global] Update failed: {pkg_name} → will try in .venv", Colors.YELLOW)
            else:
                results[pkg_lower] = 'ok'
                print_status("✓", f"[global] Already installed: {pkg_name} ({global_pkgs[pkg_lower]})", Colors.GREEN)

    return results


def install_conflicts_in_venv(packages: List[str], conflict_packages: List[str]) -> None:
    """Установка пакетов с конфликтами в .venv"""
    if not conflict_packages:
        return

    print_banner("Installing Conflicts in Virtual Environment", Colors.YELLOW)
    ensure_uv()

    # Получаем список пакетов для установки в venv
    pkgs_for_venv = []
    for pkg in packages:
        pkg_name = pkg.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
        if pkg_name.lower() in {c.lower() for c in conflict_packages}:
            pkgs_for_venv.append(pkg)

    if not pkgs_for_venv:
        return

    print_status("📦", f"Installing {len(pkgs_for_venv)} conflicting packages in .venv...", Colors.CYAN)

    # Используем lock-файл подход
    temp_req = Path("temp_requirements.in")
    temp_req.write_text("\n".join(pkgs_for_venv), encoding="utf-8")

    compile_cmd = [
        "pip", "compile",
        str(temp_req),
        "-o", LOCK_FILE,
        "--python", str(VENV_PYTHON),
        "--generate-hashes"
    ]

    res_lock = run_uv(compile_cmd, capture=False, timeout=300)
    temp_req.unlink(missing_ok=True)

    if res_lock.returncode != 0:
        print_status("⚠", "Lock file generation failed, using direct install", Colors.YELLOW)
        _install_venv_direct(pkgs_for_venv)
        return

    print_status("✓", f"Lock file created: {LOCK_FILE}", Colors.GREEN)

    sync_cmd = [
        "pip", "sync",
        LOCK_FILE,
        "--python", str(VENV_PYTHON)
    ]

    res_sync = run_uv(sync_cmd, capture=False, timeout=600)

    if res_sync.returncode == 0:
        print_status("✓", "Conflict packages installed in .venv", Colors.GREEN)
    else:
        print_status("⚠", "UV sync failed, trying atomic fallback", Colors.YELLOW)
        _install_venv_direct(pkgs_for_venv)


def _install_venv_direct(packages: List[str]) -> None:
    """Атомарная установка каждого пакета в .venv"""
    failed = []
    for pkg in packages:
        result = install_package_atomic(pkg)
        if not result:
            failed.append(pkg)

    if failed:
        print_status("✗", f"Failed to install {len(failed)} packages in .venv", Colors.RED)
        for pkg in failed:
            print_status("  •", pkg, Colors.RED)


# =============================================================================
# СКАНИРОВАНИЕ И МАППИНГ
# =============================================================================

def resolve_mapping(imports: Set[str]) -> List[str]:
    """Преобразование имён модулей в имена пакетов PyPI"""
    if not imports:
        return []

    resolved = set()
    unknown = []

    # Получаем локальный маппинг с обработкой ошибок
    try:
        local_dist = packages_distributions()
    except Exception as e:
        log.debug(f"Failed to get local distributions: {e}")
        local_dist = {}

    # Сначала пробуем локальный маппинг через importlib
    for mod in imports:
        if not mod or not isinstance(mod, str):
            continue
        # Валидация имени модуля
        if not validate_package_name(mod):
            log.debug(f"Invalid module name: {mod}")
            continue
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
                if r.status != 200:
                    raise urllib.error.HTTPError(REMOTE_MAPPING_URL, r.status,
                                                "HTTP Error", r.headers, None)
                data = r.read().decode("utf-8")
                for line in data.splitlines():
                    if ":" in line:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            remote_mapping[parts[0].strip()] = parts[1].strip()

                # Сохраняем в кэш
                save_cached_mapping(remote_mapping)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, UnicodeDecodeError) as e:
            log.debug(f"Failed to fetch remote mapping: {e}")
            remote_mapping = cached_mapping

        # Разрешаем неизвестные модули
        for mod in unknown:
            if mod in remote_mapping:
                pkg_name = remote_mapping[mod]
                if validate_package_name(pkg_name):
                    resolved.add(pkg_name)
            elif mod in cached_mapping:
                pkg_name = cached_mapping[mod]
                if validate_package_name(pkg_name):
                    resolved.add(pkg_name)
            else:
                # Последняя надежда - сам модуль это и есть имя пакета
                if validate_package_name(mod):
                    resolved.add(mod)
                    log.debug(f"Unknown module mapping: {mod} -> assuming package name")
                else:
                    log.warning(f"Skipping invalid package name: {mod}")

    return sorted(list(resolved))


def scan_project() -> List[str]:
    """Сканирование проекта на наличие импортов (исключая локальные пакеты)"""
    print_banner("Scanning Project Imports", Colors.BLUE)

    imports = set()
    ignore = {VENV_NAME, "__pycache__", ".git", ".idea", ".vscode",
              "build", "dist", ".pytest_cache", ".mypy_cache", "node_modules",
              ".omc", ".memorious", ".serena"}

    # Определяем локальные пакеты
    local_packages = detect_local_packages()
    if local_packages:
        print_status("🏠", f"Local packages detected (will be skipped): {', '.join(sorted(local_packages))}", Colors.MAGENTA)

    # Кэшируем имя текущего файла
    current_file_name = Path(__file__).name

    py_files = list(Path.cwd().rglob("*.py"))
    print_status("📁", f"Found {len(py_files)} Python files", Colors.CYAN)

    for py_file in py_files:
        # Игнорируем файлы в исключённых директориях
        if any(ignored in py_file.parts for ignored in ignore):
            continue

        # Игнорируем сам скрипт pydeploy
        if py_file.name == current_file_name:
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

        except SyntaxError as e:
            log.warning(f"Syntax error in {py_file}: {e}, skipping")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            log.debug(f"Failed to parse {py_file}: {e}")

    # Фильтруем stdlib, private модули и локальные пакеты
    stdlib = get_stdlib()
    external = {i for i in imports if i not in stdlib and not i.startswith("_") and i not in local_packages}

    if local_packages:
        skipped = {i for i in imports if i in local_packages}
        if skipped:
            print_status("⏭", f"Skipped local packages: {', '.join(sorted(skipped))}", Colors.MAGENTA)

    print_status("🔍", f"Found {len(external)} external dependencies", Colors.CYAN)

    # Преобразуем в имена пакетов
    packages = resolve_mapping(external)

    for pkg in packages:
        print_status("  •", pkg, Colors.RESET)

    return packages


# =============================================================================
# ПОЛУЧЕНИЕ УСТАНОВЛЕННЫХ ПАКЕТОВ
# =============================================================================

def get_installed_packages() -> Dict[str, str]:
    """Получение установленных пакетов с версиями (из .venv)"""
    result = run_uv(["pip", "freeze", "--python", str(VENV_PYTHON)], capture=True)

    if result.returncode != 0:
        # Fallback на обычный pip
        result = run([str(VENV_PYTHON), "-m", "pip", "freeze"], capture=True)

    if result.returncode != 0:
        log.warning("Failed to get installed packages list")
        return {}

    packages = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        try:
            if "==" in line:
                name, version = line.split("==", 1)
                packages[name.lower().strip()] = version.strip()
            elif "@" in line:
                # Обработка URL зависимостей (git+https://...)
                name = line.split("@")[0].strip()
                if name:
                    packages[name.lower()] = "url"
        except ValueError:
            log.debug(f"Failed to parse package line: {line}")
            continue

    return packages


def get_package_dependencies(package: str) -> Set[str]:
    """Получение зависимостей конкретного пакета"""
    result = run([str(VENV_PYTHON), "-m", "pip", "show", package], capture=True)

    if result.returncode != 0:
        log.debug(f"Package {package} not found or error getting info")
        return set()

    dependencies = set()
    for line in result.stdout.splitlines():
        if line.startswith("Requires:"):
            try:
                deps_str = line.split(":", 1)[1].strip()
                if deps_str:
                    dependencies = {d.strip().lower() for d in deps_str.split(",") if d.strip()}
            except (IndexError, ValueError):
                log.debug(f"Failed to parse dependencies for {package}")
            break

    return dependencies


def build_dependency_tree() -> Dict[str, Set[str]]:
    """Построение дерева зависимостей всех установленных пакетов"""
    installed = get_installed_packages()
    tree = {}

    # Кэшируем результаты для оптимизации
    for package in installed.keys():
        deps = get_package_dependencies(package)
        if deps:  # Сохраняем только непустые зависимости
            tree[package] = deps

    return tree


def find_orphaned_packages(required: List[str], installed: Dict[str, str]) -> List[str]:
    """Поиск пакетов, которые не нужны (не используются и не являются зависимостями)"""
    if not installed:
        return []

    required_lower = {pkg.lower() for pkg in required if pkg}
    installed_lower = set(installed.keys())

    # Строим дерево зависимостей
    dep_tree = build_dependency_tree()

    # Находим все транзитивные зависимости требуемых пакетов
    needed = set(required_lower)
    to_process = list(required_lower)
    processed = set()  # Защита от циклических зависимостей

    while to_process:
        pkg = to_process.pop()
        if pkg in processed:
            continue
        processed.add(pkg)

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


def prune_orphans(desired_packages: List[str]) -> None:
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
    try:
        response = input(f"\n{Colors.YELLOW}Remove these packages? [y/N]: {Colors.RESET}").strip().lower()
        if response != 'y':
            print_status("↩", "Prune cancelled", Colors.CYAN)
            return
    except (KeyboardInterrupt, EOFError):
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
    if not package or not isinstance(package, str):
        log.error(f"Invalid package name: {package}")
        return False

    # Валидация имени пакета перед установкой
    if not validate_package_name(package.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0]):
        log.error(f"Invalid package name format: {package}")
        return False

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


# =============================================================================
# СИНХРОНИЗАЦИЯ (ГЛОБАЛЬНО → .venv ТОЛЬКО КОНФЛИКТЫ)
# =============================================================================

def sync_dependencies(packages: List[str], force_update: bool = False) -> None:
    """Синхронизация зависимостей: глобально → .venv только при конфликтах"""
    if not packages:
        print_status("ℹ", "No packages to install", Colors.CYAN)
        return

    # Валидация списка пакетов
    valid_packages = []
    for pkg in packages:
        if not pkg or not isinstance(pkg, str):
            log.warning(f"Skipping invalid package: {pkg}")
            continue
        # Извлекаем имя пакета (без версий и экстра)
        pkg_name = pkg.split('[')[0].split('==')[0].split('>=')[0].split('<=')[0].split('>')[0].split('<')[0].strip()
        if validate_package_name(pkg_name):
            valid_packages.append(pkg)
        else:
            log.warning(f"Skipping invalid package name: {pkg}")

    if not valid_packages:
        print_status("⚠", "No valid packages to install after validation", Colors.YELLOW)
        return

    packages = valid_packages

    ensure_uv()

    # ЭТАП 1: Обновляем глобальные пакеты
    results = update_global_packages(packages, force=force_update)

    # Собираем пакеты с конфликтами
    conflict_packages = [name for name, status in results.items() if status == 'conflict']

    # ЭТАП 2: Устанавливаем конфликты в .venv
    if conflict_packages:
        install_conflicts_in_venv(packages, conflict_packages)
    else:
        print_status("✓", "All packages resolved globally, no .venv install needed", Colors.GREEN)

    # Создаём requirements.txt
    Path(REQ_FILE).write_text("\n".join(packages), encoding="utf-8")
    print_status("✓", f"Requirements saved to {REQ_FILE}", Colors.GREEN)


# =============================================================================
# ПРОВЕРКА ОКРУЖЕНИЯ
# =============================================================================

def verify_env() -> None:
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


# =============================================================================
# БЭКАП И ВОССТАНОВЛЕНИЕ
# =============================================================================

def create_backup() -> None:
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


def restore_backup() -> None:
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
        except (FileNotFoundError, json.JSONDecodeError, KeyError, PermissionError) as e:
            log.debug(f"Failed to read backup {backup.name}: {e}")
            print(f"  {idx}. {backup.name} (corrupted)")

    try:
        choice = int(input(f"\n{Colors.CYAN}Select backup to restore [1-{len(backups)}]: {Colors.RESET}"))
        if not 1 <= choice <= len(backups):
            print_status("✗", "Invalid choice", Colors.RED)
            return
    except (ValueError, KeyboardInterrupt, EOFError):
        print_status("✗", "Invalid input or cancelled", Colors.RED)
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

    except (FileNotFoundError, json.JSONDecodeError, KeyError, PermissionError, OSError) as e:
        log.error(f"Error reading backup: {e}")
        print_status("✗", f"Error reading backup: {e}", Colors.RED)


def show_stats() -> None:
    """Показать статистику окружения"""
    print_banner("Environment Statistics", Colors.CYAN)

    installed = get_installed_packages()

    print_status("📊", f"Total packages installed in .venv: {len(installed)}", Colors.CYAN)

    # Глобальные пакеты
    global_pkgs = get_global_packages()
    print_status("🌐", f"Global packages: {len(global_pkgs)}", Colors.CYAN)

    # Локальные пакеты
    local = detect_local_packages()
    if local:
        print_status("🏠", f"Local packages (skipped): {', '.join(sorted(local))}", Colors.MAGENTA)

    if Path(VENV_NAME).exists():
        try:
            venv_size = sum(f.stat().st_size for f in Path(VENV_NAME).rglob('*') if f.is_file())
            venv_size_mb = venv_size / (1024 * 1024)
            print_status("💾", f"Virtual environment size: {venv_size_mb:.1f} MB", Colors.CYAN)
        except (OSError, PermissionError) as e:
            log.debug(f"Failed to calculate venv size: {e}")
            print_status("⚠", "Could not calculate virtual environment size", Colors.YELLOW)

    if Path(LOCK_FILE).exists():
        try:
            lock_time = datetime.fromtimestamp(Path(LOCK_FILE).stat().st_mtime)
            print_status("🔒", f"Lock file updated: {lock_time.strftime('%Y-%m-%d %H:%M:%S')}", Colors.CYAN)
        except (OSError, ValueError) as e:
            log.debug(f"Failed to get lock file timestamp: {e}")

    backups = list_backups()
    print_status("💾", f"Available backups: {len(backups)}", Colors.CYAN)


# =============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# =============================================================================

def main():
    """Главная функция"""
    try:
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "sync"

        print(f"{Colors.BOLD}{Colors.MAGENTA}")
        print(f"{'='*70}")
        print(f" PyDeploy v{VERSION} - Universal Environment Synchronizer")
        print(f" Strategy: Global First -> .venv Only Conflicts")
        print(f"{'='*70}")
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

        # 1. Сканирование проекта (с автоопределением локальных пакетов)
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

            # Синхронизация: глобально → .venv только конфликты
            force_update = (cmd == "update")
            sync_dependencies(target_packages, force_update=force_update)

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
        print(f"{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}Ready! Run: {VENV_PYTHON} main.py{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}{'='*70}{Colors.RESET}")

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Operation cancelled by user{Colors.RESET}")
        sys.exit(0)

    except Exception as e:
        log.exception("Critical error occurred")
        print(f"\n{Colors.RED}{Colors.BOLD}Critical Error:{Colors.RESET} {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
