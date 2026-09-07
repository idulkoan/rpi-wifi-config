import os
import dbus
import html
import time
import threading
import re
import socket
from flask import Flask, jsonify, render_template, request, Response
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

AUTH_USER = os.environ.get("WIFI_USER", "admin")
AUTH_PASS = os.environ.get("WIFI_PASS", "")

NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJ_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_DEV_IFACE = "org.freedesktop.NetworkManager.Device"
NM_DEV_WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_AP_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
NM_SETTINGS_IFACE = "org.freedesktop.NetworkManager.Settings"
NM_SETTINGS_OBJ = "/org/freedesktop/NetworkManager/Settings"
NM_CONN_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
NM_ACTIVE_CONN_IFACE = "org.freedesktop.NetworkManager.Connection.Active"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"

NM_DEVICE_STATE = {
    0: "Unknown", 10: "Unmanaged", 20: "Unavailable", 30: "Disconnected",
    40: "Prepare", 50: "Config", 60: "Need Auth", 70: "IP Config",
    80: "IP Check", 90: "Secondaries", 100: "Activated",
    110: "Deactivating", 120: "Failed",
}

NM_ACTIVE_STATE_ACTIVATED = dbus.UInt32(4)
NM_ACTIVE_STATE_FAILED = dbus.UInt32(7)

_connect_lock = threading.Lock()


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not AUTH_PASS:
            return Response("WIFI_PASS environment variable not set.", 503)
        auth = request.authorization
        if not auth or auth.username != AUTH_USER or auth.password != AUTH_PASS:
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="Wi-Fi Config"'},
            )
        return f(*args, **kwargs)
    return decorated


def get_bus():
    return dbus.SystemBus()


def get_nm():
    bus = get_bus()
    nm_obj = bus.get_object(NM_BUS_NAME, NM_OBJ_PATH)
    return dbus.Interface(nm_obj, NM_IFACE)


def get_wlan0():
    bus = get_bus()
    nm = get_nm()
    for dev_path in nm.GetDevices():
        dev_obj = bus.get_object(NM_BUS_NAME, dev_path)
        props = dbus.Interface(dev_obj, DBUS_PROPS_IFACE)
        iface = str(props.Get(NM_DEV_IFACE, "Interface"))
        if iface == "wlan0":
            return dev_obj, props
    return None, None


def get_wlan0_path():
    bus = get_bus()
    nm = get_nm()
    for dev_path in nm.GetDevices():
        dev_obj = bus.get_object(NM_BUS_NAME, dev_path)
        props = dbus.Interface(dev_obj, DBUS_PROPS_IFACE)
        iface = str(props.Get(NM_DEV_IFACE, "Interface"))
        if iface == "wlan0":
            return dev_path
    return None


def get_wifi_status():
    bus = get_bus()
    dev_obj, props = get_wlan0()
    if dev_obj is None:
        return {"error": "wlan0 not found"}

    state_code = int(props.Get(NM_DEV_IFACE, "State"))
    state_str = NM_DEVICE_STATE.get(state_code, f"Unknown ({state_code})")

    result = {
        "interface": "wlan0",
        "state": state_str,
        "state_code": state_code,
        "ssid": None,
        "frequency": None,
        "signal": None,
        "ip": None,
    }

    try:
        ap_path = str(props.Get(NM_DEV_WIRELESS_IFACE, "ActiveAccessPoint"))
        if ap_path and ap_path != "/":
            ap_obj = bus.get_object(NM_BUS_NAME, ap_path)
            ap_props = dbus.Interface(ap_obj, DBUS_PROPS_IFACE)
            ssid_bytes = ap_props.Get(NM_AP_IFACE, "Ssid")
            result["ssid"] = html.escape(
                bytes(ssid_bytes).decode("utf-8", errors="replace")
            )
            result["frequency"] = int(ap_props.Get(NM_AP_IFACE, "Frequency"))
            result["signal"] = int(ap_props.Get(NM_AP_IFACE, "Strength"))
    except dbus.DBusException:
        pass

    try:
        ip4_path = str(props.Get(NM_DEV_IFACE, "Ip4Config"))
        if ip4_path and ip4_path != "/":
            ip4_obj = bus.get_object(NM_BUS_NAME, ip4_path)
            ip4_props = dbus.Interface(ip4_obj, DBUS_PROPS_IFACE)
            addresses = ip4_props.Get(
                "org.freedesktop.NetworkManager.IP4Config", "AddressData"
            )
            if addresses:
                result["ip"] = str(addresses[0]["address"])
    except dbus.DBusException:
        pass

    return result


def get_access_points():
    bus = get_bus()
    dev_obj, props = get_wlan0()
    if dev_obj is None:
        return []

    wireless = dbus.Interface(dev_obj, NM_DEV_WIRELESS_IFACE)

    try:
        wireless.RequestScan({})
        time.sleep(3)
    except dbus.DBusException:
        pass

    ap_paths = wireless.GetAllAccessPoints()
    results = []
    seen_ssids = set()

    for ap_path in ap_paths:
        try:
            ap_obj = bus.get_object(NM_BUS_NAME, ap_path)
            ap_props = dbus.Interface(ap_obj, DBUS_PROPS_IFACE)
            ssid_bytes = ap_props.Get(NM_AP_IFACE, "Ssid")
            ssid = bytes(ssid_bytes).decode("utf-8", errors="replace").strip()
            if not ssid or ssid in seen_ssids:
                continue
            seen_ssids.add(ssid)
            strength = int(ap_props.Get(NM_AP_IFACE, "Strength"))
            frequency = int(ap_props.Get(NM_AP_IFACE, "Frequency"))
            rsn_flags = int(ap_props.Get(NM_AP_IFACE, "RsnFlags"))
            wpa_flags = int(ap_props.Get(NM_AP_IFACE, "WpaFlags"))
            if rsn_flags or wpa_flags:
                security = "WPA2" if rsn_flags else "WPA"
            else:
                security = "Open"
            results.append({
                "ssid": html.escape(ssid),
                "signal": strength,
                "frequency": frequency,
                "security": security,
                "band": "5GHz" if frequency >= 5000 else "2.4GHz",
            })
        except dbus.DBusException:
            continue

    results.sort(key=lambda x: x["signal"], reverse=True)
    return results


def get_saved_connections():
    bus = get_bus()
    settings_obj = bus.get_object(NM_BUS_NAME, NM_SETTINGS_OBJ)
    settings = dbus.Interface(settings_obj, NM_SETTINGS_IFACE)

    results = []
    for conn_path in settings.ListConnections():
        try:
            conn_obj = bus.get_object(NM_BUS_NAME, conn_path)
            conn = dbus.Interface(conn_obj, NM_CONN_IFACE)
            s = conn.GetSettings()
            conn_type = str(s["connection"]["type"])
            if conn_type != "802-11-wireless":
                continue
            ssid_bytes = s["802-11-wireless"]["ssid"]
            ssid = bytes(ssid_bytes).decode("utf-8", errors="replace")
            conn_id = str(s["connection"]["id"])
            uuid = str(s["connection"]["uuid"])
            results.append({
                "id": html.escape(conn_id),
                "uuid": uuid,
                "ssid": html.escape(ssid),
                "path": str(conn_path),
            })
        except (dbus.DBusException, KeyError):
            continue

    return results


def find_connection_by_ssid(ssid):
    bus = get_bus()
    settings_obj = bus.get_object(NM_BUS_NAME, NM_SETTINGS_OBJ)
    settings = dbus.Interface(settings_obj, NM_SETTINGS_IFACE)

    for conn_path in settings.ListConnections():
        try:
            conn_obj = bus.get_object(NM_BUS_NAME, conn_path)
            conn = dbus.Interface(conn_obj, NM_CONN_IFACE)
            s = conn.GetSettings()
            if str(s["connection"]["type"]) != "802-11-wireless":
                continue
            ssid_bytes = s["802-11-wireless"]["ssid"]
            existing_ssid = bytes(ssid_bytes).decode("utf-8", errors="replace")
            if existing_ssid == ssid:
                return conn_obj, s, str(conn_path)
        except (dbus.DBusException, KeyError):
            continue
    return None, None, None


def build_settings(ssid, passphrase, existing_id=None, existing_uuid=None):
    ssid_bytes = dbus.Array(
        [dbus.Byte(b) for b in ssid.encode("utf-8")],
        signature=dbus.Signature("y")
    )

    conn_section = dbus.Dictionary({
        dbus.String("type"): dbus.String("802-11-wireless"),
        dbus.String("id"):   dbus.String(existing_id if existing_id else ssid),
    }, signature=dbus.Signature("sv"))

    if existing_uuid:
        conn_section[dbus.String("uuid")] = dbus.String(existing_uuid)

    wireless_section = dbus.Dictionary({
        dbus.String("ssid"):   ssid_bytes,
        dbus.String("mode"):   dbus.String("infrastructure"),
        dbus.String("hidden"): dbus.Boolean(False),
    }, signature=dbus.Signature("sv"))

    ipv4_section = dbus.Dictionary({
        dbus.String("method"): dbus.String("auto"),
    }, signature=dbus.Signature("sv"))

    ipv6_section = dbus.Dictionary({
        dbus.String("method"): dbus.String("auto"),
    }, signature=dbus.Signature("sv"))

    settings = dbus.Dictionary({
        dbus.String("connection"):       conn_section,
        dbus.String("802-11-wireless"):  wireless_section,
        dbus.String("ipv4"):             ipv4_section,
        dbus.String("ipv6"):             ipv6_section,
    }, signature=dbus.Signature("sa{sv}"))

    if passphrase:
        security_section = dbus.Dictionary({
            dbus.String("key-mgmt"): dbus.String("wpa-psk"),
            dbus.String("psk"):      dbus.String(passphrase),
        }, signature=dbus.Signature("sv"))
        settings[dbus.String("802-11-wireless-security")] = security_section

    return settings


def wait_for_activation(bus, active_conn_path, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ac_obj = bus.get_object(NM_BUS_NAME, active_conn_path)
            ac_props = dbus.Interface(ac_obj, DBUS_PROPS_IFACE)
            state = ac_props.Get(NM_ACTIVE_CONN_IFACE, "State")
            if state == NM_ACTIVE_STATE_ACTIVATED:
                return True
            if state == NM_ACTIVE_STATE_FAILED:
                return False
        except dbus.DBusException:
            pass
        time.sleep(1)
    return False


def validate_ssid(ssid):
    if not ssid or len(ssid) > 32:
        return "SSID must be between 1 and 32 characters."
    return None


def validate_passphrase(psk):
    if psk and (len(psk) < 8 or len(psk) > 63):
        return "WPA passphrase must be between 8 and 63 characters."
    return None


def connect_to_network(ssid, passphrase):
    bus = get_bus()
    nm = get_nm()

    wlan0_path = get_wlan0_path()
    if wlan0_path is None:
        return False, "wlan0 not found."

    dev_obj, dev_props = get_wlan0()
    rollback_conn_path = None
    try:
        active_conn_path = str(dev_props.Get(NM_DEV_IFACE, "ActiveConnection"))
        if active_conn_path and active_conn_path != "/":
            ac_obj = bus.get_object(NM_BUS_NAME, active_conn_path)
            ac_props = dbus.Interface(ac_obj, DBUS_PROPS_IFACE)
            rollback_conn_path = str(
                ac_props.Get(NM_ACTIVE_CONN_IFACE, "Connection")
            )
    except dbus.DBusException:
        pass

    existing_conn, existing_settings, existing_path = find_connection_by_ssid(ssid)

    try:
        if existing_conn is not None:
            existing_id = str(existing_settings["connection"]["id"])
            existing_uuid = str(existing_settings["connection"]["uuid"])
            settings = build_settings(ssid, passphrase, existing_id, existing_uuid)
            existing_conn.Update(settings)
            target_conn_path = existing_path
        else:
            settings = build_settings(ssid, passphrase)
            settings_obj = bus.get_object(NM_BUS_NAME, NM_SETTINGS_OBJ)
            settings_iface = dbus.Interface(settings_obj, NM_SETTINGS_IFACE)
            target_conn_path = str(settings_iface.AddConnection(settings))

        active_path = str(nm.ActivateConnection(
            dbus.ObjectPath(target_conn_path),
            dbus.ObjectPath(wlan0_path),
            dbus.ObjectPath("/"),
        ))

        success = wait_for_activation(bus, active_path, timeout=30)

        if success:
            return True, f"Connected to {ssid} successfully."

        if rollback_conn_path:
            try:
                nm.ActivateConnection(
                    dbus.ObjectPath(rollback_conn_path),
                    dbus.ObjectPath(wlan0_path),
                    dbus.ObjectPath("/"),
                )
            except dbus.DBusException:
                pass
        return False, f"Could not connect to {ssid}. Previous connection restored."

    except dbus.DBusException as e:
        return False, f"D-Bus error: {str(e)}"


def delete_connection_by_uuid(uuid):
    bus = get_bus()
    settings_obj = bus.get_object(NM_BUS_NAME, NM_SETTINGS_OBJ)
    settings = dbus.Interface(settings_obj, NM_SETTINGS_IFACE)

    for conn_path in settings.ListConnections():
        try:
            conn_obj = bus.get_object(NM_BUS_NAME, conn_path)
            conn = dbus.Interface(conn_obj, NM_CONN_IFACE)
            s = conn.GetSettings()
            if str(s["connection"]["uuid"]) != uuid:
                continue
            if str(s["connection"]["type"]) != "802-11-wireless":
                return False, "Refusing to delete non-Wi-Fi connection."
            conn.Delete()
            return True, "Connection deleted."
        except (dbus.DBusException, KeyError):
            continue

    return False, "Connection not found."


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return render_template(
        "index.html",
        hostname=os.environ.get("HOSTNAME_DISPLAY", socket.gethostname()),
    )


@app.route("/api/status")
@require_auth
def api_status():
    try:
        return jsonify(get_wifi_status())
    except dbus.DBusException as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan")
@require_auth
def api_scan():
    try:
        return jsonify(get_access_points())
    except dbus.DBusException as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connections")
@require_auth
def api_connections():
    try:
        return jsonify(get_saved_connections())
    except dbus.DBusException as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/connect", methods=["POST"])
@require_auth
def api_connect():
    if not _connect_lock.acquire(blocking=False):
        return jsonify({
            "success": False,
            "message": "Another connection attempt is already in progress."
        }), 409

    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "Invalid request body."}), 400

        ssid = data.get("ssid", "").strip()
        passphrase = data.get("passphrase", "").strip()

        err = validate_ssid(ssid)
        if err:
            return jsonify({"success": False, "message": err}), 400

        err = validate_passphrase(passphrase)
        if err:
            return jsonify({"success": False, "message": err}), 400

        success, message = connect_to_network(ssid, passphrase)
        return jsonify({"success": success, "message": message})

    finally:
        _connect_lock.release()


@app.route("/api/connections/<uuid>", methods=["DELETE"])
@require_auth
def api_delete_connection(uuid):
    if not re.match(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        uuid, re.IGNORECASE
    ):
        return jsonify({"success": False, "message": "Invalid UUID."}), 400

    try:
        success, message = delete_connection_by_uuid(uuid)
        return jsonify({"success": success, "message": message})
    except dbus.DBusException as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8090, debug=False)
