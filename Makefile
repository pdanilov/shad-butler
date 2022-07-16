
IMAGE = shad-botik
REGISTRY = cr.yandex/crps5tmfd0qqeb1jvvqh
REMOTE = $(REGISTRY)/$(IMAGE)

test-lint:
	pytest -vv --asyncio-mode=auto --pycodestyle --flakes main.py

test-key:
	pytest -vv --asyncio-mode=auto -s -k $(KEY) test.py

test-cov:
	pytest -vv --asyncio-mode=auto --cov-report html --cov main test.py

image:
	docker build -t $(IMAGE) .

push:
	docker tag $(IMAGE) $(REMOTE)
	docker push $(REMOTE)

deploy:
	yc serverless container revision deploy \
		--container-name shad-botik \
		--image cr.yandex/crps5tmfd0qqeb1jvvqh/shad-botik:latest \
		--cores 1 \
		--memory 256MB \
		--concurrency 10 \
		--execution-timeout 30s \
		--service-account-id ajedo3dbrjria8hidtsl \
		--folder-name shad-botik
