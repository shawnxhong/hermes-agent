ARG PARENT
FROM ${PARENT} AS build
USER root
WORKDIR /build
COPY module-echo-cancel.c config.h ./
COPY include/ ./include/
RUN gcc -shared -fPIC -O2 -g -Wall -Wextra -Wno-unused-parameter \
    -I. -Iinclude -Iinclude/pipewire-0.3 -Iinclude/spa-0.2 \
    module-echo-cancel.c -l:libpipewire-0.3.so.0 \
    -o libpipewire-module-echo-cancel.so
FROM ${PARENT}
COPY --from=build /build/libpipewire-module-echo-cancel.so /usr/lib/x86_64-linux-gnu/pipewire-0.3/libpipewire-module-echo-cancel.so
COPY provenance.json /opt/pipewire-aec-fix/provenance.json
