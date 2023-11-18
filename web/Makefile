.PHONY: update bundle

update:
	docker push $$(docker load < $$(nix-build --no-out-link) | sed -En 's/Loaded image: (\S+)/\1/p')

bundle:
	chmod +w static/js static/css templates

# authorized.js
	HASH=$$(esbuild static/js/authorized.js --bundle --minify | sha256sum | head -c8 ; echo '') && \
	esbuild static/js/authorized.js --bundle --minify --sourcemap --charset=utf8 --outfile=static/js/menu.$$HASH.js && \
	find templates -type f -exec sed -i 's|src="/static/js/authorized.js" type="module"|src="/static/js/menu.'$$HASH'.js"|g' {} \;

# style.css
	HASH=$$(esbuild static/css/style.css --bundle --minify | sha256sum | head -c8 ; echo '') && \
	esbuild static/css/style.css --bundle --minify --sourcemap --charset=utf8 --outfile=static/css/style.$$HASH.css && \
	find templates -type f -exec sed -i 's|href="/static/css/style.css"|href="/static/css/style.'$$HASH'.css"|g' {} \;
