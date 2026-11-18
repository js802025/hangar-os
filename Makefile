ENGINE   ?= docker
BASE     ?= localhost/hangar-base:dev
PLATFORMS ?= linux/amd64,linux/arm64

.PHONY: help base base-multiarch smoke clean

help:
	@echo "make base            build the base agent image for this machine"
	@echo "make base-multiarch  build amd64+arm64 (needs buildx; add PUSH=1 to push)"
	@echo "make smoke           M1 smoke test: start an agent, drive it over MCP"
	@echo "make clean           remove all hangar agent containers and volumes"

base:
	$(ENGINE) build -t $(BASE) -f images/base/Containerfile images

base-multiarch:
	docker buildx build --platform $(PLATFORMS) -t $(BASE) -f images/base/Containerfile images $(if $(PUSH),--push,)

smoke:
	uv run python tests/smoke_m1.py

clean:
	-$(ENGINE) rm -f $$($(ENGINE) ps -aq --filter label=dev.hangar.agent)
	-$(ENGINE) volume rm $$($(ENGINE) volume ls -q --filter name=hangar-)
