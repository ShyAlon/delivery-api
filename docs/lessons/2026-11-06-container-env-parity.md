# The container env boundary needs writer parity, not reader parity

679 taught the image settings reader to strip owner dotenv quotes. That fixed
validation, but the preview still handed the OWNER FILE to docker --env-file,
and Docker passes values literally with no quote stripping. The owner's writer
(settings.py save_confirmed, set_key quote_mode='always') quotes every value, so
the container received DELIVERY_DATABASE_URL with literal quote characters and
full_entry refused at startup (exit 2: PostgreSQL engine session persistence
required) even though validate had approved the same file.

The preview now writes a derived container env file holding exactly the parsed
values validate approved (unquoted, atomic, mode 600, in the private run state
dir) and passes that to docker. Validation and container input are one value
stream again. The owner file is never rewritten for this.

Related surfacing gaps fixed in the same pass: the readiness failure now names
the container exit code and the exact docker logs command, and the
already-exists error distinguishes a running container (docker stop) from an
exited one (docker rm), instead of telling the owner to stop a dead container.
