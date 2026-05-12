# 🧬 DNA Comparison System - Demo Commands & Audit Results

Este documento contiene los comandos exactos para ejecutar la demo y los resultados de la auditoría.

## 📋 Requisitos Previos

```bash
# En Windows, asegúrate de tener:
# - Docker Desktop instalado y ejecutando
# - Python 3.12+
# - Redis CLI (opcional, para inspeccionar)

# Verificar instalación
docker --version
docker-compose --version
python --version
```

---

## 🚀 DEMO: Ejecución Normal (Sin Fallos)

### Paso 1: Preparar Entrada

```bash
# Crear directorio de entrada
mkdir -p control-plane/inputs

# Crear archivos de prueba (100 bytes cada uno, idénticos)
python create_test_inputs.py

# Verificar que los archivos existen y tienen igual tamaño
dir control-plane/inputs
# Debería mostrar:
#   A.clean  200 bytes
#   B.clean  200 bytes
```

### Paso 2: Iniciar el Sistema

```bash
# Construir imágenes y iniciar todos los servicios
docker-compose -f docker-compose.local.yml up --build

# Esperar hasta que veas logs tipo:
# node-1 | {"ts":"2024-05-11T12:00:00Z","level":"INFO","event":"worker.start","msg":"concurrency=2"}
# node-2 | {"ts":"2024-05-11T12:00:01Z","level":"INFO","event":"worker.start","msg":"concurrency=2"}
# node-3 | {"ts":"2024-05-11T12:00:01Z","level":"INFO","event":"worker.start","msg":"concurrency=2"}

# Presionar Ctrl+C para detener (o en otro terminal continuar...)
```

### Paso 3: Inspeccionar Progreso (en otra terminal)

```bash
# Ver logs del líder
docker-compose -f docker-compose.local.yml logs -f node-1 | grep -E "leader|reduce|chunk.done"

# Esperar 10-30 segundos para que se procesen todos los chunks
# Verás algo como:
# node-1 | {"event":"leader.acquired","msg":"Became leader epoch=1"}
# node-1 | {"event":"chunk.done","chunk_id":"chunk_000000"}
# node-1 | {"event":"reduce.start","msg":"Concatenating 1 partials"}
# node-1 | {"event":"reduce.done","msg":"similarity=..."}
```

### Paso 4: Obtener Resultados

```bash
# Archivo final de similitud
dir control-plane/runs/run-001/final
# Debería contener: similarity_map.out

# Ver contenido del archivo de similitud (200 caracteres 'X' = 100% match)
Get-Content control-plane/runs/run-001/final/similarity_map.out
# Salida esperada: XXXX...XXXX (100% caracteres coinciden)

# Ver resumen JSON
type control-plane/runs/run-001/summary.json
# Salida esperada:
# {
#   "run_id": "run-001",
#   "total_bases": 200,
#   "matches": 200,
#   "mismatches": 0,
#   "similarity_percentage": 100.0,
#   "output_file": ".../similarity_map.out",
#   "chunks": 1,
#   "finished_at": 1234567890.123
# }
```

---

## 💀 DEMO: Matar Worker (Simular Fallo)

### Escenario: Worker muere, líder recama el job

```bash
# Terminal 1: Iniciar todo
docker-compose -f docker-compose.local.yml up --build

# Terminal 2: Mientras se procesa, encontrar PID de un worker
docker ps
# Copiar CONTAINER ID de node-2 o node-3

# Matar el contenedor (simula crash)
docker kill <CONTAINER_ID>
# Ejemplo: docker kill a1b2c3d4e5f6

# Observar en Terminal 1:
# - Verás el worker dead después de 10 segundos (NODE_DEAD_AFTER_SECONDS)
# - Líder detecta: {"event":"node.dead","msg":"Node node-2 silent for 10.5s"}
# - Líder recama jobs: {"event":"jobs.reclaimed","msg":"reclaimed=1 stale jobs"}
# - Otro worker toma el job
# - Run se completa normalmente
```

### Comando Completo con Matar:

```bash
# Terminal 1
docker-compose -f docker-compose.local.yml up --build &
COMPOSE_PID=$!

# Esperar a que se estabilice
Start-Sleep -Seconds 5

# Terminal 2 simulada en el mismo script
$CONTAINER=$(docker ps --filter "label=com.docker.compose.service=node-2" -q)
Start-Sleep -Seconds 15
docker kill $CONTAINER

# Esperar a que se recupere
Start-Sleep -Seconds 30

# Ver resultado
Get-Content control-plane/runs/run-001/summary.json | Select-String "similarity"
```

---

## 👑 DEMO: Matar Líder (Simular Fallo del Maestro)

### Escenario: Líder muere, elección de nuevo líder

```bash
# Terminal 1: Iniciar todo
docker-compose -f docker-compose.local.yml up --build

# Terminal 2: Esperar a que node-1 sea líder (~2 segundos)
docker-compose -f docker-compose.local.yml logs node-1 | grep "leader.acquired"

# Esperar a que comience a procesar jobs
Start-Sleep -Seconds 10

# Matar el líder
docker kill $(docker ps --filter "label=com.docker.compose.service=node-1" -q)

# Observar en Terminal 1:
# - node-2 o node-3 ganan la elección
# - {"event":"leader.acquired","msg":"Became leader epoch=2"}
# - Nuevo líder comienza reclamación de jobs
# - Sistema recupera y completa normalmente
```

**Esperado**: El sistema tolera la muerte del líder y elige uno nuevo automáticamente.

---

## 🔄 DEMO: Reconstruir Final (Rebuild)

### Escenario: Reconstruir archivo final después de muerte

```bash
# Para forzar un rebuild (por ejemplo, si se corrompió el archivo final):
# 1. Limpiar el archivo final
rm -Force control-plane/runs/run-001/final/similarity_map.out

# 2. Enviar comando de rebuild (simulado para demo)
# En código actual, solo líder puede lanzar esto; usarías Redis CLI:
docker exec $(docker ps --filter "name=redis" -q) \
  redis-cli XADD stream:commands:run-001 "*" \
  cmd '{"op":"REBUILD_FINAL"}'

# 3. Esperar a que líder reciba y procese el comando
# Verás: {"event":"reduce.start","msg":"Concatenating..."}

# 4. Verificar nuevo resultado
Get-Content control-plane/runs/run-001/final/similarity_map.out
```

---

## 🔍 DEMO: Inspeccionar Redis

### Ver estado actual del sistema

```bash
# Conectar a Redis
docker exec $(docker ps --filter "name=redis" -q) redis-cli

# Dentro de redis-cli:

# Ver nodos activos
SMEMBERS nodes:active
# Salida: node-1, node-2, node-3

# Ver información del líder
HGETALL leader:lock
# Salida: node_id, priority, epoch, token, acquired_at

# Ver estado del run
GET runs:run-001:status
# Salida: RUNNING o COMPLETED

# Ver estadísticas acumuladas
HGETALL runs:run-001:stats
# Salida: matches, mismatches, total_bases, chunks_done

# Ver chunks completados
SMEMBERS runs:run-001:chunks:done
# Salida: chunk_000000, chunk_000001, ...

# Ver información de cada chunk
HGETALL chunk:run-001:chunk_000000
# Salida: status, checksum, matches, mismatches, completed_at

# Salir
QUIT
```

---

## 📊 DEMO: Ver Logs Estructurados

### Filtrar logs JSON por evento

```bash
# Logs de líder
docker-compose -f docker-compose.local.yml logs node-1 | \
  findstr /C:"leader.acquired" /C:"reduce.start" /C:"reduce.done"

# Logs de worker
docker-compose -f docker-compose.local.yml logs node-2 | \
  findstr /C:"chunk.done" /C:"chunk.upload_failed" /C:"input.download"

# Logs de todos los nodos
docker-compose -f docker-compose.local.yml logs | \
  findstr /C:"error" /C:"ERROR" /C:"failed"

# Ver evento específico en JSON (más fácil de parsear)
docker-compose -f docker-compose.local.yml logs | \
  Select-String '"level":"ERROR"'
```

---

## ✅ DEMO: Resumen de Resultado Final

```bash
# Después de que el sistema se complete:

# 1. Ver archivo de similitud
$similarities = Get-Content control-plane/runs/run-001/final/similarity_map.out
Write-Host "Archivo de similitud ($($similarities.Length) bytes):"
Write-Host $similarities.Substring(0, [Math]::Min(100, $similarities.Length))

# 2. Ver resumen JSON
$summary = Get-Content control-plane/runs/run-001/summary.json | ConvertFrom-Json
Write-Host "Similitud: $($summary.similarity_percentage)%"
Write-Host "Matches: $($summary.matches) / $($summary.total_bases)"
Write-Host "Chunks procesados: $($summary.chunks)"

# 3. Ver archivos en control-plane
dir control-plane/runs/run-001 -Recurse
```

---

## 🧪 AUDIT RESULTS - TESTS

```bash
# Ejecutar todos los tests
python -m pytest tests/ -v

# Resultado esperado:
# 16 PASSED ✅
# 3 SKIPPED (Leader election - requiere Redis live)

# Ejecutar tests específicos
python -m pytest tests/test_processor.py -v         # Procesamiento
python -m pytest tests/test_manifest.py -v          # Manifest
python -m pytest tests/test_worker_idempotency.py -v # Idempotencia
python -m pytest tests/test_idempotency_fix.py -v   # Fixes
```

---

## 🐛 BUGS ENCONTRADOS Y CORREGIDOS

Ver `AUDIT_REPORT.md` para detalles completos. Resumen:

| Bug | Severidad | Estado |
|-----|-----------|--------|
| Race condition en leader election | 🔴 CRITICAL | ✅ FIXED |
| Re-procesamiento de chunks DONE | 🟠 HIGH | ✅ FIXED |
| Reclamación sin verificación | 🟠 HIGH | ✅ FIXED |
| Validación gap en concatenación | 🟡 MEDIUM | Documented |
| XAUTOCLAIM sin retry | 🟡 MEDIUM | Documented |

---

## 📝 NOTAS IMPORTANTES

### Local Mode
- Volumen compartido garantiza consistencia
- No hay race conditions reales en file:// operations
- Perfecto para desarrollo y demos

### Cloud Mode (SCP Real)
- Implementar retry de SCP está completo
- Timeouts configurables
- Documentar riesgos en guía de deployment

### Escalabilidad
- Demo optimizado para 3 nodos + chunks de 1 MiB
- En producción: 100s de nodos, chunks de 32-128 MiB
- Redis debe estar en cluster o sentinel setup

### Monitoreo
- Logs JSON para indexado en ELK/CloudWatch
- Metrics: heartbeat, reclamación, duration_ms
- Alertas en: chunk.failed, leader.lost, node.dead

---

## 🎯 PRÓXIMOS PASOS

1. ✅ **Auditoría completada** - Ver AUDIT_REPORT.md
2. ✅ **Bugs críticos arreglados** - Todos pasan tests
3. ⏳ **Stress test** - Simular 100+ workers
4. ⏳ **Deployment en AWS/Azure** - Adaptar SCP a cloud storage
5. ⏳ **UI/Dashboard** - Frontend para monitorear runs

---

**Última actualización**: 11 de Mayo de 2026  
**Status**: 🟢 **LISTO PARA DEMO**
