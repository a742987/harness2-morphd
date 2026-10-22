.PHONY: test lint verify build dry-run web-install web-test web-build consumer-test visual

ENGINE=.venv/bin

test: consumer-test web-test

consumer-test:
	$(ENGINE)/ruff check morphd tests
	$(ENGINE)/pytest -q

web-install:
	npm --prefix consumer/web install
	npm --prefix packages/cli install
	npm --prefix consumer/electron install --ignore-scripts

web-test:
	npm --prefix consumer/web test
	npm --prefix packages/cli test
	npm --prefix packages/tray test
	npm --prefix consumer/electron test

web-build:
	npm --prefix consumer/web run build
	npm --prefix packages/cli run build
	npm --prefix consumer/electron run build

lint:
	$(ENGINE)/ruff check morphd tests
	npm --prefix consumer/web run lint
	npm --prefix packages/cli run lint
	npm --prefix consumer/electron run lint

verify:
	sh scripts/verify_vendor.sh
	$(ENGINE)/ruff check morphd tests
	$(ENGINE)/pytest -q
	npm --prefix consumer/web run lint
	npm --prefix consumer/web test
	npm --prefix packages/cli test
	npm --prefix packages/tray test
	npm --prefix consumer/electron test

build:
	$(ENGINE)/python -m build
	npm --prefix consumer/web run build
	npm --prefix packages/cli run build
	npm --prefix consumer/electron run build

dry-run:
	$(ENGINE)/morphd run --dry-run --split test

visual:
	node consumer/web/scripts/visual-check.mjs "$(URL)" "$(OUT)"
