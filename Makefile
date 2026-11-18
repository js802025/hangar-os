ENGINE   ?= docker
BASE     ?= localhost/hangar-base:dev
PLATFORMS ?= linux/amd64,linux/arm64

FLAVORS  ?= research coding office

GATEWAY  ?= localhost/hangar-gateway:dev

.PHONY: help base base-multiarch flavors flavor gateway images smoke clean

help:
	@echo "make base            build the base agent image for this machine"
	@echo "make base-multiarch  build amd64+arm64 (needs buildx; add PUSH=1 to push)"
	@echo "make flavors         build every flavor in images/flavors/ on top of base"
	@echo "make flavor F=office build one flavor"
	@echo "make gateway         build the egress gateway used by agents with an allowlist"
	@echo "make images          base + gateway + every flavor"
	@echo "make smoke           M1 smoke test: start an agent, drive it over MCP"
	@echo "make clean           remove all hangar agent containers and volumes"

base:
	$(ENGINE) build -t $(BASE) -f images/base/Containerfile images

base-multiarch:
	docker buildx build --platform $(PLATFORMS) -t $(BASE) -f images/base/Containerfile images $(if $(PUSH),--push,)

gateway:
	$(ENGINE) build -t $(GATEWAY) -f images/gateway/Containerfile images/gateway

images: base gateway flavors

flavors:
	@for f in $(FLAVORS); do $(MAKE) --no-print-directory flavor F=$$f || exit 1; done

flavor:
	$(ENGINE) build --build-arg BASE=$(BASE) -t localhost/hangar-$(F):dev -f images/flavors/$(F)/Containerfile images/flavors/$(F)

smoke:
	uv run python tests/smoke_m1.py

clean:
	-$(ENGINE) rm -f $$($(ENGINE) ps -aq --filter label=dev.hangar.agent)
	-$(ENGINE) volume rm $$($(ENGINE) volume ls -q --filter name=hangar-)
