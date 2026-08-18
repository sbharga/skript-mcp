#!/bin/sh
set -eu

case "${RCON_PASSWORD:-}" in
    ""|*[!A-Za-z0-9._-]*)
        echo "RCON_PASSWORD must be 8-128 characters using A-Z, a-z, 0-9, dot, underscore, or hyphen" >&2
        exit 1
        ;;
esac

if [ "${#RCON_PASSWORD}" -lt 8 ] || [ "${#RCON_PASSWORD}" -gt 128 ]; then
    echo "RCON_PASSWORD must be 8-128 characters" >&2
    exit 1
fi

case "${PUID:-1000}:${PGID:-1000}" in
    *[!0-9:]*|:*|*:)
        echo "PUID and PGID must be numeric" >&2
        exit 1
        ;;
esac
PUID=${PUID:-1000}
PGID=${PGID:-1000}

mkdir -p /server/plugins/Skript/scripts /server/logs
cp /opt/skript-server/paper.jar /server/paper.jar
rm -f /server/plugins/*.jar
cp /opt/skript-server/plugins/*.jar /server/plugins/

printf 'eula=true\n' > /server/eula.txt
cat > /server/server.properties <<EOF
accepts-transfers=false
allow-flight=false
broadcast-rcon-to-ops=false
difficulty=peaceful
enable-command-block=false
enable-jmx-monitoring=false
enable-query=false
enable-rcon=true
enable-status=true
enforce-secure-profile=true
force-gamemode=false
gamemode=creative
generate-structures=false
level-name=world
level-type=minecraft:flat
max-players=1
motd=Skript MCP validation server
online-mode=true
prevent-proxy-connections=false
pvp=false
query.port=25565
rate-limit=0
rcon.password=${RCON_PASSWORD}
rcon.port=25575
server-ip=
server-port=25565
simulation-distance=3
spawn-animals=false
spawn-monsters=false
spawn-npcs=false
spawn-protection=0
sync-chunk-writes=true
view-distance=3
white-list=true
EOF

chown "${PUID}:${PGID}" \
    /server \
    /server/eula.txt \
    /server/paper.jar \
    /server/server.properties \
    /server/logs \
    /server/plugins \
    /server/plugins/*.jar \
    /server/plugins/Skript \
    /server/plugins/Skript/scripts

# Intentionally split JAVA_OPTS so standard JVM flags can be supplied as one value.
# shellcheck disable=SC2086
exec setpriv --reuid="${PUID}" --regid="${PGID}" --clear-groups \
    java ${JAVA_OPTS:--Xms1G -Xmx2G} -jar /server/paper.jar --nogui
