# Dashboard Integration — Backend Implementation

## Resumen de cambios implementados

El backend ha sido actualizado para ser completamente compatible con el dashboard Next.js. Todos los datos se escriben en Redis con el formato exacto que el frontend espera.

---

## 1. Heartbeat de Nodos — `nodes:{nodeId}`

### Formato
```json
{
  "nodeId": "aws-node-1",
  "status": "ACTIVE",
  "priority": 100,
  "canBeLeader": true,
  "cpuUsage": 72,
  "memoryUsage": 58,
  "concurrency": 4,
  "activeJobs": 2,
  "completedJobs": 26,
  "failedJobs": 0,
  "provider": "AWS",
  "timestamp": 1747267623000
}
```

### Implementación
- **Archivo**: `heartbeat.py`
- **Escrito como**: STRING con TTL 30 segundos (via `SETEX`)
- **Campo `provider`**: Se lee de la variable de entorno `PROVIDER` (valores: `"AWS"`, `"AZURE"`, `"GCP"`, `"LOCAL"`)
- **CPU/Memoria**: Se calcula usando `psutil` library
- **Timestamp**: En milisegundos Unix (ms, no segundos)
- **Intervalo**: Configurable via `HEARTBEAT_INTERVAL_SECONDS` (default: 2s)

### Uso de environment
```bash
export NODE_ID=aws-node-1
export PROVIDER=AWS
export HEARTBEAT_INTERVAL_SECONDS=2
```

---

## 2. Set de Nodos Activos — `nodes:active`

### Implementación
- `SADD nodes:active {nodeId}` cada vez que se escribe heartbeat
- `SREM nodes:active {nodeId}` cuando el nodo muere (después de `NODE_DEAD_AFTER_SECONDS` sin heartbeat)

---

## 3. Líder Actual — `leader:lock`

### Formato
```json
{
  "node_id": "aws-node-1",
  "priority": 100,
  "epoch": 7,
  "token": "abc123...",
  "acquired_at": 1747267623.45
}
```

### Implementación
- **Archivo**: `leader_election.py`
- **TTL**: `LEADER_LOCK_TTL_MS` (default: 7000 ms)
- **Renovación**: Token-based safe renewal via Lua script
- El frontend puede leer este campo para saber quién es el líder

---

## 4. Stats del Run — `runs:{runId}:stats` (HASH)

### Formato
```bash
HSET runs:run-001:stats \
  status               RUNNING \
  totalBases           3000000000 \
  totalChunks          90 \
  completedChunks      62 \
  pendingChunks        23 \
  processingChunks     3 \
  failedChunks         1 \
  retryingChunks       1 \
  matches              2985642000 \
  mismatches           14358000 \
  similarityPercentage 99.54 \
  startedAt            2026-05-14T08:00:00Z
```

### Implementación
- **Archivo**: `redis_state.py`, método `update_run_stats()`
- **Actualizado por**: Leader en cada tick (`_tick()`)
- **Nombres exactos**: Todos en camelCase como se muestra arriba
- **Status valores**: `IDLE | PREPARING | RUNNING | PAUSED | REBUILDING | COMPLETED | FAILED | CANCELLED`

---

## 5. Estado de cada Chunk — `chunk:{runId}:{chunkId}` (HASH)

### Formato
```bash
HSET chunk:run-001:chunk_000062 \
  status       DONE \
  matches      33552732 \
  mismatches   1700 \
  checksum     sha256:abc123 \
  worker       aws-node-1 \
  completed_at 1747267800.50
```

### Status posibles
- `PENDING` — En queue, esperando ser procesado
- `PROCESSING` — Actualmente siendo procesado
- `DONE` — Completado exitosamente
- `FAILED` — Procesamiento falló
- `RETRY` — Marcado para reintentar

---

## 6. Eventos — `stream:events:{runId}` (REDIS STREAM)

### Formato
```bash
XADD stream:events:run-001 * \
  event '{"timestamp":"2026-05-14T09:00:00Z","severity":"success","eventType":"chunk_completed","nodeId":"aws-node-1","chunkId":"chunk_000062","message":"Chunk completado exitosamente","matches":33552732,"mismatches":1700}'
```

### Tipos de eventos publicados
- `run_started` — El líder asumió el run
- `run_cancelled` — El run fue cancelado
- `run_completed` — El run finalizó exitosamente
- `chunk_completed` — Un chunk se completó
- `chunk_failed` — Un chunk falló en procesamiento o upload
- `worker_status_changed` — Worker cambió estado (ACTIVE → PAUSED, etc.)

### Severities
- `"info"` — Información general
- `"success"` — Operación exitosa
- `"warning"` — Advertencia, pero el run continúa
- `"error"` — Error crítico

---

## 7. Comandos del Dashboard — `stream:commands:{runId}`

### Payload (Formato Dashboard Nuevo)

**Comando a Worker:**
```json
{
  "type": "worker",
  "command": "pause",
  "nodeId": "gcp-node-1",
  "runId": "run-001",
  "commandId": "uuid-...",
  "issuedAt": "2026-05-14T09:00:00Z"
}
```

**Comando a Run:**
```json
{
  "type": "run",
  "command": "pause_run",
  "runId": "run-001",
  "commandId": "uuid-...",
  "issuedAt": "2026-05-14T09:00:00Z"
}
```

### Comandos soportados

| `type`   | `command`        | Acción                                            |
|----------|------------------|---------------------------------------------------|
| `worker` | `pause`          | Pausar worker específico                          |
| `worker` | `resume`         | Reanudar worker específico                        |
| `worker` | `drain`          | Drenar worker (termina jobs actuales, no toma nuevos) |
| `worker` | `disable`        | Deshabilitar worker completamente                 |
| `run`    | `pause_run`      | Pausar todo el run                                |
| `run`    | `resume_run`     | Reanudar el run                                   |
| `run`    | `retry_failed`   | Reintentar chunks fallidos                        |
| `run`    | `rebuild_output` | Reconstruir output final                          |
| `run`    | `cancel_run`     | Cancelar el run                                   |

### Implementación
- **Archivo**: `commands.py`, función `apply_command()`
- **Leído por**: El líder en cada tick (`_process_commands()`)
- **Formato**: Mapea automáticamente desde `type`/`command` a acciones internas
- **Retrocompatibilidad**: También soporta el formato legacy `op`-based

---

## 8. Configuración Necesaria

### Variables de Entorno

```bash
# Identidad del nodo
NODE_ID=aws-node-1                    # Obligatorio
PROVIDER=AWS                           # AWS | AZURE | GCP | LOCAL (default: LOCAL)
NODE_PRIORITY=100                      # Mayor número = mayor prioridad para ser líder
CAN_BE_LEADER=true                     # ¿Puede este nodo ser líder?
LEADER_CAN_PROCESS=false               # ¿El líder procesa chunks o solo gestiona?

# Concurrencia
WORKER_CONCURRENCY=auto                # "auto" o número específico
MAX_CONCURRENCY=4                      # Máximo permitido

# Redis
REDIS_URL=redis://redis:6379/0         # Conexión a Redis

# Run
RUN_ID=run-001                         # ID del run actual

# Heartbeat
HEARTBEAT_INTERVAL_SECONDS=2           # Cada cuántos segundos enviar heartbeat
NODE_DEAD_AFTER_SECONDS=10             # Cuándo marcar un nodo como muerto

# Control Plane (SCP/SSH)
CONTROL_PLANE_HOST=10.0.0.5            # O "local" para modo demo
CONTROL_PLANE_USER=ubuntu
CONTROL_PLANE_BASE_DIR=/data/dna-demo
CONTROL_PLANE_SSH_KEY=/path/to/key

# Procesamiento
CHUNK_SIZE_BYTES=33554432              # 32 MB default
```

---

## 9. Flujo de Datos Esperado

```
Frontend (Next.js)
    ↓
    └─→ POST /api/dashboard/commands/{runId}
        └─→ Publica en stream:commands:{runId}

Backend (Python)
    ↓
    ├─→ Lee heartbeat cada 2s → nodes:{nodeId} (STRING, TTL 30s)
    ├─→ Lee stream:commands:{runId} → aplica acciones
    ├─→ Procesa chunks → chunk:{runId}:{chunkId} (HASH)
    ├─→ Publica eventos → stream:events:{runId} (STREAM)
    └─→ Actualiza stats → runs:{runId}:stats (HASH)

Frontend (Next.js polling cada 5s)
    ↓
    └─→ GET /api/dashboard/snapshot/{runId}
        └─→ Lee todo de Redis, renderiza

```

---

## 10. Testeo

### 1. Verificar formato de heartbeat
```bash
redis-cli
> GET nodes:aws-node-1
> (Should be valid JSON with camelCase fields)
```

### 2. Verificar stats del run
```bash
redis-cli
> HGETALL runs:run-001:stats
> (Should have all required fields)
```

### 3. Publicar comando de prueba
```bash
redis-cli
> XADD stream:commands:run-001 * cmd '{"type":"worker","command":"pause","nodeId":"aws-node-1"}'
> (Backend should log receipt and pause the worker)
```

### 4. Ver eventos
```bash
redis-cli
> XRANGE stream:events:run-001 - +
> (Should see events like chunk_completed, run_started, etc.)
```

---

## 11. Notas Importantes

1. **Timestamp en milisegundos**: Todos los `timestamp` en el heartbeat están en **milisegundos Unix**, no segundos.

2. **Nombres camelCase**: Todos los campos de heartbeat y stats usan **camelCase** exacto: `nodeId`, `cpuUsage`, `canBeLeader`, etc. No `node_id`, `cpu_usage`.

3. **TTL de heartbeat (30s)**: El frontend marca un nodo como muerto si no actualiza heartbeat en 30+ segundos. El backend escribe con `SETEX` automáticamente.

4. **Similitud porcentual**: El campo `similarityPercentage` es `matches / total_bases * 100`, redondeado a 2 decimales.

5. **Strings en HSET**: Los valores en HASH se escriben como strings vía `str()`, no como números. Redis los devuelve como strings al leer.

6. **Comando `retry_failed`**: Activa el flag `retry_failed_flag` en metadata; el sistema luego re-publica chunks fallidos.

---

## Archivos Modificados

- ✅ `config.py` — Agregado `PROVIDER`
- ✅ `heartbeat.py` — Nuevo formato con TTL 30s, camelCase, ms timestamp
- ✅ `redis_state.py` — `write_heartbeat()` ahora usa SETEX; agregado `update_run_stats()` y `publish_event()`
- ✅ `commands.py` — Mapea `type`/`command` a acciones; soporta nuevos comandos
- ✅ `worker.py` — Publica eventos de chunk completion; rastrear cambios de estado
- ✅ `leader.py` — Publica eventos run start/complete; actualiza stats cada tick
- ✅ `models.py` — Agregado `RunStatus.PAUSED` para soportar pausa

---

## Próximos pasos recomendados

1. Desplegar y ejecutar un run de prueba
2. Verificar en Redis que todos los formatos son correctos
3. Verificar que el frontend recibe los datos correctamente via GET `/api/dashboard/snapshot/{runId}`
4. Testear comandos del frontend (pause, resume, cancel)
5. Verificar eventos en `stream:events:{runId}`

