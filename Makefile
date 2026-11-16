ifeq ($(OS),Windows_NT)
SHELL := E:/Git/bin/bash.exe
.SHELLFLAGS := -ec
endif

IMAGE := box-health-agent
CONTAINER := health-agent
VOLUME := health-agent-data

.PHONY: build up down logs demo-cpu demo-ram demo-disk demo-disk-clear demo-kill demo-zombie demo-memory-climbing demo-network-down demo-network-up

build:
	docker build -t $(IMAGE) .

up:
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true
	docker run -d --name $(CONTAINER) -p 8000:8000 \
		-v $(VOLUME):/app/data \
		--tmpfs /test:size=50m \
		$(IMAGE)
	@echo "dashboard: http://localhost:8000"

down:
	docker rm -f $(CONTAINER)

logs:
	docker logs -f $(CONTAINER)

demo-cpu:
	MSYS_NO_PATHCONV=1 docker exec -d $(CONTAINER) stress-ng --cpu 0 --timeout 100
	@echo "watch http://localhost:8000 - cpu_high should open ~60-70s in, close when stress-ng exits at 100s"

demo-ram:
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true
	docker run -d --name $(CONTAINER) -p 8000:8000 \
		-v $(VOLUME):/app/data \
		--tmpfs /test:size=50m \
		--memory 1g -e RAM_HIGH_THRESHOLD=35 \
		$(IMAGE)
	sleep 5
	MSYS_NO_PATHCONV=1 docker exec -d $(CONTAINER) stress-ng --vm 1 --vm-bytes 600M --vm-keep --timeout 45
	@echo "watch http://localhost:8000 - ram_high should open within ~10s, close ~45s later"
	@echo "run 'make up' afterward to restore the real 90% threshold"

demo-memory-climbing:
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true
	docker run -d --name $(CONTAINER) -p 8000:8000 \
		-v $(VOLUME):/app/data \
		--tmpfs /test:size=50m \
		-e MEMORY_CLIMB_SAMPLES=4 \
		-e MEMORY_CLIMB_BUCKET_SECONDS=8 \
		-e MEMORY_CLIMB_MIN_RISING_TRANSITIONS=3 \
		-e MEMORY_CLIMB_CHECK_INTERVAL_SECONDS=5 \
		$(IMAGE)
	sleep 5
	MSYS_NO_PATHCONV=1 docker cp demo/leak.py $(CONTAINER):/tmp/leak.py
	MSYS_NO_PATHCONV=1 docker exec -d $(CONTAINER) python3 /tmp/leak.py
	@echo "memory_climbing should open for this script's PID within ~60-70s - check http://localhost:8000"
	@echo "run 'make up' afterward to restore the real one-minute buckets"

demo-disk:
	MSYS_NO_PATHCONV=1 docker exec $(CONTAINER) dd if=/dev/zero of=/test/fill.bin bs=1M count=46
	@echo "disk_high should open for subject /test - run 'make demo-disk-clear' to close it"

demo-disk-clear:
	MSYS_NO_PATHCONV=1 docker exec $(CONTAINER) rm -f /test/fill.bin

demo-kill:
	MSYS_NO_PATHCONV=1 docker exec -d $(CONTAINER) stress-ng --cpu 1 --timeout 120
	sleep 5
	MSYS_NO_PATHCONV=1 docker exec $(CONTAINER) python3 -c "import psutil, os, signal; [os.kill(p.pid, signal.SIGKILL) for p in psutil.process_iter(['name']) if p.info['name'] == 'stress-ng']"
	@echo "process_died should open for subject stress-ng"

demo-zombie:
	MSYS_NO_PATHCONV=1 docker cp demo/zombie.py $(CONTAINER):/tmp/zombie.py
	MSYS_NO_PATHCONV=1 docker exec -d $(CONTAINER) python3 /tmp/zombie.py
	@echo "zombie should open ~30s in and close ~40s in - check http://localhost:8000"

demo-network-down:
	docker network disconnect bridge $(CONTAINER)
	@echo "network_lost should open within ~45s (3 x 15s probe interval)"
	@echo "note: the host port mapping is also down while disconnected - use 'make demo-network-check' from inside the container"

demo-network-check:
	MSYS_NO_PATHCONV=1 docker exec $(CONTAINER) python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read().decode())"

demo-network-up:
	docker network connect bridge $(CONTAINER)
	@echo "network_lost should close on the next probe tick"
