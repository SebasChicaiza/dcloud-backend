# Guía de Configuración de Variables de Entorno

## ¿Qué son las variables de entorno?

Las **variables de entorno** son parámetros que defines **antes** de ejecutar un programa. El programa las lee al iniciar y las usa para configurarse.

En este proyecto, se usan para definir:
- ID del nodo (`NODE_ID`)
- Proveedor cloud (`PROVIDER`)
- URL de Redis (`REDIS_URL`)
- ID del run (`RUN_ID`)
- Y muchos otros parámetros

---

## 4 Formas de Configurar Variables de Entorno

### 📌 Opción 1: Exportar en la Terminal (rápido, por sesión)

#### En PowerShell (Windows):
```powershell
# Navega al directorio del proyecto
cd 'c:\Comp Distribuida\adn_cloud\dcloud-backend'

# Exporta las variables
$env:NODE_ID = "local-node-1"
$env:PROVIDER = "LOCAL"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:RUN_ID = "run-001"
$env:HEARTBEAT_INTERVAL_SECONDS = "2"

# Ejecuta el programa
python -m dna_node.main
```

**Ventaja**: Rápido para testing  
**Desventaja**: Se pierden cuando cierras la terminal

#### En Bash/Linux/macOS:
```bash
cd /path/to/dcloud-backend

export NODE_ID=local-node-1
export PROVIDER=LOCAL
export REDIS_URL=redis://localhost:6379/0
export RUN_ID=run-001
export HEARTBEAT_INTERVAL_SECONDS=2

python -m dna_node.main
```

---

### 📌 Opción 2: Archivo `.env` (recomendado para desarrollo)

**Crea un archivo `.env` en la raíz del proyecto:**

```
Ruta: c:\Comp Distribuida\adn_cloud\dcloud-backend\.env
```

**Contenido:**
```env
# Identidad del nodo
NODE_ID=local-node-1
PROVIDER=LOCAL
RUN_ID=run-001

# Redis
REDIS_URL=redis://localhost:6379/0

# Configuración de líder
NODE_PRIORITY=100
CAN_BE_LEADER=true
LEADER_CAN_PROCESS=true

# Concurrencia
WORKER_CONCURRENCY=auto
MAX_CONCURRENCY=4

# Heartbeat
HEARTBEAT_INTERVAL_SECONDS=2
NODE_DEAD_AFTER_SECONDS=10

# Control Plane
CONTROL_PLANE_HOST=local
CONTROL_PLANE_USER=ubuntu
CONTROL_PLANE_BASE_DIR=/control-plane
CONTROL_PLANE_LOCAL_DIR=/control-plane

# Local
LOCAL_DATA_DIR=/worker-cache
INPUT_A_NAME=A.clean
INPUT_B_NAME=B.clean
CHUNK_SIZE_BYTES=33554432

# SCP
SCP_CONNECT_TIMEOUT_SECONDS=10
SCP_RETRIES=3

# Jobs
JOB_MIN_IDLE_MS=30000
```

**El código carga esto automáticamente** (en `config.py`):
```python
try:
    from dotenv import load_dotenv
    load_dotenv()  # ← Carga .env automáticamente
except ImportError:
    pass
```

**Usar:**
```powershell
cd 'c:\Comp Distribuida\adn_cloud\dcloud-backend'
python -m dna_node.main
```

**Ventaja**: Persiste entre ejecuciones, no interfiere con terminal  
**Desventaja**: Debes recordar crear el archivo

---

### 📌 Opción 3: Docker Compose (producción, múltiples nodos)

**Archivo:** `docker-compose.local.yml`

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    container_name: dna-redis
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes

  # Nodo 1 — AWS (puede ser líder)
  worker-aws:
    build: .
    container_name: dna-worker-aws
    environment:
      NODE_ID: aws-node-1
      PROVIDER: AWS
      REDIS_URL: redis://redis:6379/0
      RUN_ID: run-001
      NODE_PRIORITY: 100
      CAN_BE_LEADER: true
      LEADER_CAN_PROCESS: true
      WORKER_CONCURRENCY: 4
      HEARTBEAT_INTERVAL_SECONDS: 2
      CONTROL_PLANE_HOST: local
      CONTROL_PLANE_LOCAL_DIR: /control-plane
      LOCAL_DATA_DIR: /worker-cache
    volumes:
      - ./control-plane:/control-plane
      - ./worker-cache-aws:/worker-cache
    depends_on:
      - redis
    command: python -m dna_node.main

  # Nodo 2 — GCP (no puede ser líder)
  worker-gcp:
    build: .
    container_name: dna-worker-gcp
    environment:
      NODE_ID: gcp-node-1
      PROVIDER: GCP
      REDIS_URL: redis://redis:6379/0
      RUN_ID: run-001
      NODE_PRIORITY: 50
      CAN_BE_LEADER: false
      WORKER_CONCURRENCY: 4
      HEARTBEAT_INTERVAL_SECONDS: 2
      CONTROL_PLANE_HOST: local
      CONTROL_PLANE_LOCAL_DIR: /control-plane
      LOCAL_DATA_DIR: /worker-cache
    volumes:
      - ./control-plane:/control-plane
      - ./worker-cache-gcp:/worker-cache
    depends_on:
      - redis
    command: python -m dna_node.main

  # Nodo 3 — AZURE
  worker-azure:
    build: .
    container_name: dna-worker-azure
    environment:
      NODE_ID: azure-node-1
      PROVIDER: AZURE
      REDIS_URL: redis://redis:6379/0
      RUN_ID: run-001
      NODE_PRIORITY: 75
      CAN_BE_LEADER: true
      WORKER_CONCURRENCY: 2
      HEARTBEAT_INTERVAL_SECONDS: 2
      CONTROL_PLANE_HOST: local
      CONTROL_PLANE_LOCAL_DIR: /control-plane
      LOCAL_DATA_DIR: /worker-cache
    volumes:
      - ./control-plane:/control-plane
      - ./worker-cache-azure:/worker-cache
    depends_on:
      - redis
    command: python -m dna_node.main

volumes:
  redis-data:
```

**Usar:**
```bash
docker-compose -f docker-compose.local.yml up
```

**Ventaja**: Escalable, múltiples nodos en paralelo, reproducible  
**Desventaja**: Necesita Docker

---

### 📌 Opción 4: Script de inicio (múltiples nodos locales)

**Archivo:** `run_workers.sh` (en Linux/macOS) o `run_workers.ps1` (en Windows)

#### Para PowerShell (Windows):
```powershell
# c:\Comp Distribuida\adn_cloud\dcloud-backend\run_workers.ps1

# Asegúrate de permitir scripts
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Nodo 1 — AWS (puede ser líder)
Write-Host "Iniciando AWS node-1..."
$env:NODE_ID = "aws-node-1"
$env:PROVIDER = "AWS"
$env:NODE_PRIORITY = "100"
$env:CAN_BE_LEADER = "true"
Start-Process python -ArgumentList "-m dna_node.main" -NoNewWindow

# Nodo 2 — GCP (no puede ser líder)
Write-Host "Iniciando GCP node-1..."
$env:NODE_ID = "gcp-node-1"
$env:PROVIDER = "GCP"
$env:NODE_PRIORITY = "50"
$env:CAN_BE_LEADER = "false"
Start-Process python -ArgumentList "-m dna_node.main" -NoNewWindow

# Nodo 3 — AZURE
Write-Host "Iniciando AZURE node-1..."
$env:NODE_ID = "azure-node-1"
$env:PROVIDER = "AZURE"
$env:NODE_PRIORITY = "75"
$env:CAN_BE_LEADER = "true"
Start-Process python -ArgumentList "-m dna_node.main" -NoNewWindow

Write-Host "Todos los nodos iniciados. Presiona Ctrl+C para detener."
```

**Usar:**
```powershell
.\run_workers.ps1
```

#### Para Bash (Linux/macOS):
```bash
#!/bin/bash
# c:\Comp Distribuida\adn_cloud\dcloud-backend\run_workers.sh

# Nodo 1 — AWS
export NODE_ID=aws-node-1
export PROVIDER=AWS
export NODE_PRIORITY=100
export CAN_BE_LEADER=true
python -m dna_node.main &
AWS_PID=$!

# Nodo 2 — GCP
export NODE_ID=gcp-node-1
export PROVIDER=GCP
export NODE_PRIORITY=50
export CAN_BE_LEADER=false
python -m dna_node.main &
GCP_PID=$!

# Nodo 3 — AZURE
export NODE_ID=azure-node-1
export PROVIDER=AZURE
export NODE_PRIORITY=75
export CAN_BE_LEADER=true
python -m dna_node.main &
AZURE_PID=$!

echo "Nodos corriendo: AWS=$AWS_PID GCP=$GCP_PID AZURE=$AZURE_PID"
wait
```

**Usar:**
```bash
chmod +x run_workers.sh
./run_workers.sh
```

**Ventaja**: Lanza múltiples nodos en paralelo  
**Desventaja**: Más complejo de debuggear

---

## Comparativa: Cuál usar

| Escenario | Opción | Por qué |
|-----------|--------|--------|
| Desarrollo local, 1 nodo | `.env` | Fácil, no repites variables |
| Testing rápido | Terminal directo | Ves los valores mientras escribes |
| Producción, múltiples nodos | Docker Compose | Escalable, reproducible |
| Demo, múltiples nodos locales | Script (bash/ps1) | Lanzas varios workers a la vez |

---

## Variables Esenciales vs Opcionales

### ✅ OBLIGATORIAS (sin default)
```env
NODE_ID=local-node-1                    # Identificador único
PROVIDER=LOCAL                          # AWS | AZURE | GCP | LOCAL
REDIS_URL=redis://localhost:6379/0      # Conexión a Redis
RUN_ID=run-001                          # ID del run actual
```

### ⚙️ OPCIONALES (tienen valores por defecto)
```env
# Líder
NODE_PRIORITY=100                       # (default: 100)
CAN_BE_LEADER=true                      # (default: true)
LEADER_CAN_PROCESS=true                 # (default: false)

# Concurrencia
WORKER_CONCURRENCY=auto                 # (default: auto)
MAX_CONCURRENCY=4                       # (default: 4)

# Heartbeat
HEARTBEAT_INTERVAL_SECONDS=2            # (default: 2)
NODE_DEAD_AFTER_SECONDS=10              # (default: 10)

# Control Plane
CONTROL_PLANE_HOST=local                # (default: local)
CONTROL_PLANE_USER=ubuntu               # (default: ubuntu)

# Paths
LOCAL_DATA_DIR=/worker-cache            # (default: /worker-cache)
CONTROL_PLANE_LOCAL_DIR=/control-plane  # (default: /control-plane)
```

---

## Validación: ¿Las variables se cargaron?

Crea un script de test `test_env.py`:

```python
import os
from dna_node.config import Config

try:
    cfg = Config.from_env()
    print("✅ Variables cargadas exitosamente:")
    print(f"   NODE_ID: {cfg.node_id}")
    print(f"   PROVIDER: {cfg.provider}")
    print(f"   REDIS_URL: {cfg.redis_url}")
    print(f"   RUN_ID: {cfg.run_id}")
    print(f"   CAN_BE_LEADER: {cfg.can_be_leader}")
except KeyError as e:
    print(f"❌ Falta variable de entorno: {e}")
except Exception as e:
    print(f"❌ Error: {e}")
```

Ejecuta:
```powershell
python test_env.py
```

---

## Resumen: Pasos rápidos

### Opción A — `.env` (recomendado)
```powershell
# 1. Crea .env en la raíz
# 2. Copia el contenido de arriba
# 3. Ejecuta:
python -m dna_node.main
```

### Opción B — Terminal
```powershell
$env:NODE_ID = "local-node-1"
$env:PROVIDER = "LOCAL"
$env:REDIS_URL = "redis://localhost:6379/0"
python -m dna_node.main
```

### Opción C — Docker
```bash
docker-compose -f docker-compose.local.yml up
```

---

## Notas Importantes

1. **El orden importa**: Define las variables ANTES de ejecutar el programa
2. **Las mayúsculas/minúsculas**: En Linux importa; en Windows no
3. **Espacios en valores**: Si tienes espacios, usa comillas: `"mi valor"`
4. **.env es ignorado por Git**: Crea `.env.example` sin valores sensibles para el repo

---

## Próximos pasos

- Elige una opción y crea las variables
- Verifica con `test_env.py` que se carguen
- Ejecuta `python -m dna_node.main`
- Monitorea los logs en tiempo real

