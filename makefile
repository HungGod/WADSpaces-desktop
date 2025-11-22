IMAGE ?= ghcr.io/wad/workspace-video-game-dev:latest

build:
	docker build -t $(IMAGE) .

rebuild:
	docker build --no-cache -t $(IMAGE) .

run: 
	docker compose up -d

stop:
	docker compose down
