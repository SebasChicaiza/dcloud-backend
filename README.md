# DNA Distributed Compute — Backend (Nodes)

Backend de los **compute nodes** para una demo académica de Computación
Distribuida en la nube. Compara dos archivos `.fna` ya normalizados posición
por posición y produce:

- Un mapa de similitud (`X` = misma base, `.` = base distinta).
- Un porcentaje global de similitud.

Cada nodo corre la **misma imagen Docker** y puede actuar como **líder** o
**worker**: el líder se elige dinámicamente vía Redis y los workers consumen
jobs desde Redis Streams.

> Este repo es solo el backend distribuido. El frontend/dashboard se conecta
> a Redis para leer estado (heartbeats, manifest, stats, eventos).

---

## Arquitectura

```
                ┌───────────────────────────┐
                │      Control Plane VM     │
                │  ─────────────────────    │
                │  Redis (Docker)           │
                │  Frontend / Dashboard     │
                │  /data/dna-demo/          │
                │    inputs/A.clean         │
                │    inputs/B.clean         │
                │    runs/<run_id>/         │
                │      partials/            │
                │      partials_meta/       │
                │      final/               │
                │      summary.json         │
                └─────────────▲─────────────┘
                              │ SCP
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────┴─────┐          ┌────┴─────┐          ┌────┴─────┐
   │ Node 1   │          │ Node 2   │          │ Node 3   │
   │ leader   │  Redis   │ worker   │  Redis   │ worker   │
   │ +worker  │◄────────►│          │◄────────►│          │
   └──────────┘          └──────────┘          └──────────┘
```

- **Redis** guarda solo metadata: locks, jobs, heartbeats, chunks, stats,
  eventos, comandos. **Nunca** guarda ADN ni partials.
- **Archivos grandes** (inputs, partials, output final) viven en disco.
- **SCP** mueve partials hacia el Control Plane.
- En el demo local (`docker-compose.local.yml`) usamos un **volumen compartido**
  como Control Plane simulado (modo `CONTROL_PLANE_HOST=local`).

---

## Estructura

```
dna_node/
  main.py              # entrypoint
  config.py            # carga env
  redis_state.py       # wrapper de redis
  leader_election.py   # SET NX PX + renovación segura por token
  heartbeat.py         # estado + info estática del nodo (CPU, OS, mem, ...)
  leader.py            # manifest, reclaim, comandos, reduce
  worker.py            # XREADGROUP + ProcessPoolExecutor + SCP + XACK
  processor.py         # comparación vectorizada con NumPy
  scp_client.py        # scp/ssh + modo local (volumen compartido)
  manifest.py          # plan determinístico de chunks
  commands.py          # PAUSE/RESUME/DRAIN/DISABLE/RETRY_CHUNK/...
  models.py            # dataclasses + enums de estados
  logging_config.py    # logs JSON estructurados

tests/                 # pytest
Dockerfile
docker-compose.local.yml
requirements.txt
.env.example
```

---

## 1. Correr Redis + 3 nodos en local (Docker Compose)

```bash
# 1) Preparar carpetas e inputs
mkdir -p control-plane/inputs
cp /ruta/a/A.clean control-plane/inputs/A.clean
cp /ruta/a/B.clean control-plane/inputs/B.clean

# 2) Levantar todo (Redis + 3 nodos)
docker compose -f docker-compose.local.yml up --build

# 3) Verás:
#    - 1 nodo declarándose leader
#    - 3 nodos trabajando como workers
#    - partials apareciendo en ./control-plane/runs/run-001/partials/
#    - al terminar: ./control-plane/runs/run-001/final/similarity_map.out
#                   ./control-plane/runs/run-001/summary.json
```

## 2. Preparar `A.clean` y `B.clean`

Los archivos deben:

- Estar **normalizados/limpios** (solo bases válidas, sin headers ni saltos).
- Idealmente del **mismo tamaño**. Si difieren, el sistema usa el mínimo
  tamaño comparable y registra un warning.
- La comparación es **byte por byte posicional** (no alignment biológico).

Para una prueba sintética:

```bash
mkdir -p control-plane/inputs
head -c 10485760 /dev/urandom | tr -dc 'ACGT' | head -c 10000000 > control-plane/inputs/A.clean
cp control-plane/inputs/A.clean control-plane/inputs/B.clean
# corromper aleatoriamente algunas bases en B para que no sea 100%
python3 -c "
import os, random
data = bytearray(open('control-plane/inputs/B.clean','rb').read())
for _ in range(len(data)//20):
    i = random.randrange(len(data)); data[i] = random.choice(b'ACGT')
open('control-plane/inputs/B.clean','wb').write(bytes(data))
"
```

## 3. Demo local end-to-end

```bash
docker compose -f docker-compose.local.yml up --build
# ...esperar a que aparezca "reduce.done"
cat control-plane/runs/run-001/summary.json
```

## 4. Simular caída de un worker

```bash
# Mata un worker en pleno procesamiento:
docker compose -f docker-compose.local.yml kill node3

# El líder reclamará sus jobs pendientes (XAUTOCLAIM tras JOB_MIN_IDLE_MS).
# Los otros nodos los reprocesarán. Verifica al final que summary.json se generó.
```

## 5. Simular caída del líder

```bash
# Identifica al líder en logs (busca event=leader.acquired) y mátalo:
docker compose -f docker-compose.local.yml kill node1

# Pasados ~LEADER_LOCK_TTL_MS, otro nodo gana el lock y continúa.
# Verás "leader.acquired" en otro nodo y el run termina normalmente.
```

## 6. Inspeccionar Redis

```bash
docker compose -f docker-compose.local.yml exec redis redis-cli

> KEYS *
> GET leader:lock
> HGETALL nodes:node-1
> HGETALL nodes:node-1:info         # CPU, OS, memoria, etc.
> SMEMBERS nodes:active
> GET runs:run-001:status
> HGETALL runs:run-001:stats
> HGETALL runs:run-001:meta
> XLEN stream:jobs:run-001
> XINFO GROUPS stream:jobs:run-001
> XRANGE stream:events:run-001 - +
```

## 7. Ver partial outputs

```bash
ls control-plane/runs/run-001/partials/
# chunk_000000.out chunk_000001.out ...
hexdump -C control-plane/runs/run-001/partials/chunk_000000.out | head
# Verás solo bytes 'X' (0x58) y '.' (0x2e)
```

## 8. Reconstruir el output final manualmente

El líder lo hace automáticamente cuando todos los chunks están `DONE`. Para
forzar una reconstrucción (por ejemplo si moviste partials manualmente):

```bash
docker compose -f docker-compose.local.yml exec node1 \
  redis-cli -h redis XADD stream:commands:run-001 '*' cmd '{"op":"REBUILD_FINAL"}'
```

## 9. Limitaciones conocidas

- **Punto único de falla en la demo**: Redis y el Control Plane no están
  replicados. Si Redis cae, todo el cluster se detiene.
- Los archivos deben estar **normalizados**: si tienen headers FASTA, saltos
  de línea u otros caracteres, la comparación posicional dará resultados
  incorrectos.
- La comparación es **posicional**, no es alignment genómico real
  (Smith-Waterman, BLAST, etc.).
- **No es una prueba de paternidad** ni un test médico — es una demo de
  cómputo distribuido sobre datos biológicos.
- `XAUTOCLAIM` requiere Redis ≥ 6.2.
- Modo local (volumen compartido) no es una prueba realista de SCP/red.

## 10. Correr en la nube

1. **Control Plane VM** (AWS/Azure/GCP):
   - Instalar Docker + `redis:7-alpine` expuesto en `:6379` (con firewall
     restringido a la subnet de nodos compute).
   - Crear usuario `ubuntu` con `~/.ssh/authorized_keys` que contenga la
     clave pública de cada nodo.
   - Crear estructura: `/data/dna-demo/inputs/A.clean`, `B.clean`.
2. **Cada nodo compute**:
   - Montar la clave SSH privada como volumen → `CONTROL_PLANE_SSH_KEY`.
   - Ajustar env vars: `CONTROL_PLANE_HOST=<IP de la VM>`,
     `CONTROL_PLANE_USER=ubuntu`, `REDIS_URL=redis://<IP>:6379/0`.
   - Levantar el contenedor con la misma imagen:
     ```bash
     docker run -d --name dna-node \
       --env-file .env \
       -v $HOME/.ssh/id_rsa:/root/.ssh/id_rsa:ro \
       -v /var/lib/dna-cache:/worker-cache \
       <registry>/dna-node:latest
     ```
3. Repetir en cada VM con `NODE_ID` distinto.

---

## Tests

```bash
pip install -r requirements.txt pytest
pytest -q
# test_leader_election se salta si no hay Redis en localhost.
# Para correrlo: docker run -p 6379:6379 redis:7-alpine
```

---

## Cómo se evita perder trabajo

| Escenario           | Mecanismo                                                  |
|---------------------|------------------------------------------------------------|
| Worker muere        | Job sin `XACK` → leader hace `XAUTOCLAIM` y re-publica.    |
| Líder muere         | Lock expira (TTL) → otro nodo lo adquiere y reconstruye.   |
| SCP falla           | Reintentos con backoff; sin ACK → reclaim automático.      |
| Checksum incorrecto | El líder marca el chunk para retry y re-publica el job.    |
| Build final         | Concatenación **por `chunk_index`**, nunca por orden de    |
|                     | llegada; valida checksum de cada partial antes de unir.    |

---

## Notas de implementación

- **NumPy** en `processor.py`: la comparación vectorizada (`a == b` sobre
  `np.frombuffer`) es órdenes de magnitud más rápida que un loop Python para
  chunks de 32–128 MB. Razón documentada en el módulo.
- **Token-based lock renewal**: la renovación del lock usa un script Lua
  que solo extiende el TTL si el token sigue siendo el del nodo actual.
  Esto evita que un líder zombi pise el lock de uno nuevo.
- **Heartbeat estático vs dinámico**: la info estática del nodo
  (`hostname`, `cpu_model`, `total_memory_bytes`, `python_version`, etc.) se
  publica **una sola vez** en `nodes:{id}:info` al arranque, para que el FE
  pueda mostrarla sin que cada heartbeat la retransmita. El heartbeat normal
  (`nodes:{id}`) solo lleva métricas dinámicas: jobs actuales/completados,
  estado, último contacto, etc.
