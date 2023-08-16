.PHONY: update bundle

update:
	docker push $$(docker load < $$(nix-build --no-out-link) | sed -En 's/Loaded image: (\S+)/\1/p')

bundle:
	chmod +w static/css templates

# style.css
	HASH=$$(esbuild static/css/style.css --bundle --minify | sha256sum | head -c8 ; echo '') && \
	esbuild static/css/style.css --bundle --minify --sourcemap --charset=utf8 --outfile=static/css/style.$$HASH.css && \
	find templates -type f -exec sed -i 's|href="/static/css/style.css"|href="/static/css/style.'$$HASH'.css"|g' {} \;
