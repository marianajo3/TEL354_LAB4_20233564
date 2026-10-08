import yaml

import json
import requests
import asyncio
import aiohttp

CONTROLLER_IP = "127.0.0.1"
FLOODLIGHT_URL = "http://192.168.0"

class Student:
    def __init__(self, name, mac):
        self.name = name
        self.mac = mac.lower() 

class Service:
    def __init__(self, name, protocol, port):
        self.name = name
        self.protocol = protocol.lower()
        self.port = port

class Server:
    def __init__(self, name, ip, services_list):
        self.name = name
        self.ip = ip
        self.services = {}
        for s in services_list:
            self.services[s["name"]] = Service(s["name"], s["protocol"], s["port"])

class Course:
    def __init__(self, name, state, students_list, servers_list):
        self.name = name
        self.state = state           
        self.students = students_list  
        self.servers = servers_list   

    def add_student(self, student_name):
        self.students.append(student_name)

    def remove_student(self, student_name):
        self.students.remove(student_name)
=======
#!/usr/bin/env python3
"""TEL354 Lab 4: administrador proactivo de políticas SDN con Floodlight 1.2.
Ejecutar en la VM Controller junto a database.yaml.
"""
import asyncio
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import requests
import yaml

BASE_URL = 'http://127.0.0.1:8080'
STATIC_URL = BASE_URL + '/wm/staticflowpusher/json'
TIMEOUT = 8

@dataclass
class Student:
    name: str
    mac: str

@dataclass
class Service:
    name: str
    protocol: str
    port: int

@dataclass
class Server:
    name: str
    ip: str
    services: dict = field(default_factory=dict)

@dataclass
class Course:
    name: str
    code: str
    state: str
    students: list
    servers: list
>>>>>>> c13720a (Correccion de rutas y flows OpenFlow - Lab 4)

students_db = []
servers_db = []
courses_db = []
connections_db = {}


<<<<<<< HEAD
def load_data(filename):
    with open(filename, "r") as file:
        data = yaml.safe_load(file)
    students = [ Student(x["name"], x["mac"]) for x in data["students"] ]
    servers = [ Server(y["name"], y["ip"], y["services"]) for y in data["servers"] ]
    courses = [ Course(z["name"], z["state"], z["students"], z["servers"]) for z in data["courses"] ]
    return students, servers, courses


def get_attachment_point(controller_ip, mac):
    endpoint = '/wm/device/'
    url = 'http://' + controller_ip + ':8080' + endpoint
    try:
        response = requests.get(url, params={'mac': mac})
        res_json = response.json()
        if not res_json: return None, None
        result = res_json
        attachment_points = result.get("attachmentPoint", [])
        if not attachment_points: return None, None
        attachment_point = attachment_points
        return attachment_point["switchDPID"], attachment_point["port"]
    except Exception:
        return None, None

def get_route(controller_ip, src_mac, dst_mac):
    src_sw_dpid, src_sw_port = get_attachment_point(controller_ip, src_mac)
    dst_sw_dpid, dst_sw_port = get_attachment_point(controller_ip, dst_mac)
    if not src_sw_dpid or not dst_sw_dpid: return None
    endpoint = f'/wm/topology/route/{src_sw_dpid}/{src_sw_port}/{dst_sw_dpid}/{dst_sw_port}/json'
    url = 'http://' + controller_ip + ':8080' + endpoint
    response = requests.get(url)
    return response.json()


def check_authorization(student_obj, server_obj, service_name):
    """ Matches the lab policy requirements for authorized traffic """
    for course in courses_db:
        if course.state == "ACTIVE":
            if student_obj.name in course.students:
                for srv_info in course.servers:
                    if srv_info["name"] == server_obj.name:
                        if service_name in srv_info["allowed_services"]:
                            return True
    return False

async def send_flow_async(session, flow_payload):
    try:
        async with session.post(FLOODLIGHT_URL, json=flow_payload) as response:
            return await response.text()
    except Exception as e:
        return str(e)

async def build_route_flows(route_data, src_mac, dst_ip, srv_mac, service_obj, handler):
    """ Deconstructs the path hops and pushes matching flows to each switch """
    flows = []
    path = route_data.get("route", []) if route_data else []
    
    for i in range(0, len(path), 2):
        if i+1 >= len(path): break
        dpid = path[i]["switch"]
        in_port = path[i]["port"]
        out_port = path[i+1]["port"]
        
        flows.append({
            "switch": dpid,
            "name": f"flow-in-{handler}-{dpid[-2:]}",
            "eth_type": "0x0800", # IPv4
            "eth_src": src_mac,
            "ipv4_dst": dst_ip,
            "ip_proto": "6" if service_obj.protocol == "tcp" else "17",
            "tp_dst": str(service_obj.port),
            "actions": f"output={out_port}"
        })
        
        flows.append({
            "switch": dpid,
            "name": f"flow-out-{handler}-{dpid[-2:]}",
            "eth_type": "0x0800",
            "ipv4_src": dst_ip,
            "eth_dst": src_mac,
            "ip_proto": "6" if service_obj.protocol == "tcp" else "17",
            "tp_src": str(service_obj.port),
            "actions": f"output={in_port}"
        })

        # Essential ARP matching to allow L2 address resolution
        flows.append({"switch": dpid, "name": f"arp-in-{handler}-{dpid[-2:]}", "eth_type": "0x0806", "eth_src": src_mac, "actions": f"output={out_port}"})
        flows.append({"switch": dpid, "name": f"arp-out-{handler}-{dpid[-2:]}", "eth_type": "0x0806", "eth_src": srv_mac, "actions": f"output={in_port}"})

    # Local switch fallback if the topology calculation is empty
    if not path:
        for dpid in ["00:00:00:00:00:00:00:01", "00:00:00:00:00:00:00:02", "00:00:00:00:00:00:00:03"]:
            flows.append({"switch": dpid, "name": f"fallback-{handler}-{dpid[-2:]}", "eth_type": "0x0800", "eth_src": src_mac, "ipv4_dst": dst_ip, "actions": "output=normal"})

    async with aiohttp.ClientSession() as session:
        tasks = [send_flow_async(session, f) for f in flows]
        await asyncio.gather(*tasks)
    print(f"[+] {len(flows)} OpenFlow entries pushed asynchronously to switches.")

def remove_route_flows(handler):
    for dpid_suffix in ["01", "02", "03", "04"]:
        for prefix in ["flow-in", "flow-out", "arp-in", "arp-out", "fallback"]:
            try: requests.delete(FLOODLIGHT_URL, json={"name": f"{prefix}-{handler}-{dpid_suffix}"})
            except Exception: pass

#creamos el menu para cursos
def menu_courses():
    print("\n--- MENU DE CURSOS ---")
    print("1) List courses (*)\n2) Show course details (*)\n3) Manage students (Add/Remove) (*)")
    opc = input(">>> ")
    if opc == "1":
        for c in courses_db: print(f"Course: {c.name} | State: {c.state}")
    elif opc == "2":
        name = input("Nombre del curso:")
        c = next((x for x in courses_db if x.name.lower() == name.lower()), None)
        if c: print(f"\nDetalles:\n - Estado: {c.state}\n - Estudiantes: {', '.join(c.students)}\n - Servidores: {[s['name'] for s in c.servers]}")
        else: print("No se encontró el curso.")
    elif opc == "3":
        name = input("Course name: ")
        c = next((x for x in courses_db if x.name.lower() == name.lower()), None)
        if c:
            action = input("1) Add student  2) Remove student\n>>> ")
            student_name = input("Student's full name: ")
            if action == "1": c.add_student(student_name); print("[+] Student added to course.")
            elif action == "2": c.remove_student(student_name); print("[+] Student removed from course.")
        else: print("[-] Course not found.")

#creamos el menu de estudiantes
def menu_students():
    print("\n--- MENU DE ALUMNOS ---")
    print("1) Crear estudiante (*)\n2) Listar estudiantes (*)\n3) Mostrar detalles de estudiantes(*)")
    opc = input(">>> ")
    if opc == "1":
        n = input("Nombre completo: ")
        m = input("MAC: ")
        students_db.append(Student(n, m))
        print("El estudiante ha sido registrado.")
    elif opc == "2":
        for s in students_db: print(f"Nombre: {s.name} | MAC: {s.mac}")
    elif opc == "3":
        name = input("Nombre del estudiante: ")
        s = next((x for x in students_db if x.name.lower() == name.lower()), None)
        if s: print(f"\nPropiedades:\n - Nombre: {s.name}\n - MAC: {s.mac}")
        else: print("No se encontro al estudiante.")

#creamos el menu de servidores y servicios
def menu_servers():
    print("\n--- MENU DE SERVICIOS y SERVIDORES ---")
    print("1) Lista de servidores (*)\n2) Muestra de servicios (*)")
    opc = input(">>> ")
    if opc == "1":
        for srv in servers_db: print(f"Server: {srv.name} | IP: {srv.ip}")
    elif opc == "2":
        name = input("Nombre del Servidor: ")
        srv = next((x for x in servers_db if x.name.lower() == name.lower()), None)
        if srv:
            for s_name, s_obj in srv.services.items(): 
                print(f" - {s_name}: Protocolo {s_obj.protocol.upper()} / Puerto {s_obj.port}")
        else: print("No se encontro el servidor.")

#creamos el menu de conexiones
def menu_connections():
    print("\n--- MENU DE CONEXIONES ---")
    print("1) Crear conexion (*)\n2) Listar conexiones (*)\n3) Mostrar detalles(Ruta)\n4) Borrar conexion (*)")
    opc = input(">>> ")
    if opc == "1":
        st_name = input("Nombre del estudiante: ")
        srv_name = input("Nombre del servidor: ")
        serv_name = input("Servicio: ")
        
        student = next((x for x in students_db if x.name.lower() == st_name.lower()), None)
        server = next((x for x in servers_db if x.name.lower() == srv_name.lower()), None)
        
        if not student or not server: 
            print("Invalido")
            return
            
        if not check_authorization(student, server, serv_name):
            print("\nERROR: El estudiante no esta autorizado.")
            return
            
        service_obj = server.services.get(serv_name)
        srv_mac = "00:00:00:00:00:0" + server.ip[-1]
        
        route_data = get_route(CONTROLLER_IP, student.mac, srv_mac)
        
        handler_name = student.name.lower().replace(" ", "")
        handler = f"h_{handler_name}_{server.name.replace(' ','').lower()}"
        
        asyncio.run(build_route_flows(route_data, student.mac, server.ip, srv_mac, service_obj, handler))
        connections_db[handler] = {
            "student": student.name, 
            "server": server.name, 
            "service": serv_name,
            "route": route_data
        }
        print(f"Conexion creada exitosamente. Handler: {handler}")
        
    elif opc == "2":
        print("\n--- Conexiones proactivas activas ---")
        for h, d in connections_db.items():
            print(f"Handler: {h} | {d['student']} -> {d['server']} [{d['service']}]")
            
    elif opc == "3":
        h = input("Ingrese el handler para ver route: ")
        if h in connections_db:
            print(f"\n--- Detalles de ruta para {h} ---")
            print(json.dumps(connections_db[h]["route"], indent=4))
        else:
            print("[-] Handler not found.")
            
    elif opc == "4":
        h = input("Handler para remover: ")
        if h in connections_db:
            remove_route_flows(h)
            del connections_db[h]
            print("Flow entries creados exitosamente.")
        else:
            print("[-] No se encontro el Handler")

#ahora este sera el menu principal
def menu():
    print("\nNetwork Policy manager de la UPSM\n")
    print("Seleccione una opción:\n1) Importar\n2) Exportar\n3) Cursos\n4) Alumnos\n5) Servidores\n6) Políticas\n7) Conexiones\n8) Salir")

def main():
    global students_db, servers_db, courses_db
    students_db, servers_db, courses_db = load_data("database.yaml")
    while True:
        menu()
        opcion = input(">>> ")
        if opcion == "3": menu_courses()
        elif opcion == "4": menu_students()
        elif opcion == "5": menu_servers()
        elif opcion == "7": menu_connections()
        elif opcion == "8":
            break
        else:
            print("Invalido")

if __name__ == "__main__":
=======
def load_data(path='database.yaml'):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    students = [Student(s['name'], s['mac'].lower()) for s in data.get('students', [])]
    servers = [Server(s['name'], str(s['ip']), {
        v['name']: Service(v['name'], str(v['protocol']).lower(), int(v['port']))
        for v in s.get('services', [])}) for s in data.get('servers', [])]
    courses = [Course(c['name'], c.get('code', ''), c['state'],
                      list(c.get('students', [])), list(c.get('servers', [])))
               for c in data.get('courses', [])]
    return students, servers, courses


def api_get(path, params=None):
    r = requests.get(BASE_URL + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def devices():
    result = api_get('/wm/device/')
    return result if isinstance(result, list) else result.get('devices', [])


def device_for_mac(mac):
    for d in devices():
        if mac.lower() in [str(x).lower() for x in d.get('mac', [])]:
            return d
    return None


def device_for_ip(ip):
    for d in devices():
        if ip in [str(x) for x in d.get('ipv4', [])]:
            return d
    return None


def attachment(device):
    if not device:
        raise RuntimeError('Dispositivo no descubierto por Floodlight')
    points = [p for p in device.get('attachmentPoint', []) if p.get('switchDPID') and p.get('port') is not None]
    if not points:
        raise RuntimeError('El dispositivo no tiene attachmentPoint conocido')
    p = points[0]
    return str(p['switchDPID']), int(p['port'])


def calculate_route(src_mac, dst_ip):
    src = device_for_mac(src_mac)
    dst = device_for_ip(dst_ip)
    if not src or not dst:
        raise RuntimeError('Floodlight no conoce la MAC del alumno o la IP del servidor. Verifica hosts, ARP y topología.')
    src_sw, src_port = attachment(src)
    dst_sw, dst_port = attachment(dst)
    dst_macs = dst.get('mac', [])
    if not dst_macs:
        raise RuntimeError('Floodlight no conoce la MAC del servidor')
    dst_mac = str(dst_macs[0]).lower()
    if src_sw == dst_sw:
        hops = [(src_sw, src_port, dst_port)]
    else:
        path = api_get(f'/wm/topology/route/{src_sw}/{src_port}/{dst_sw}/{dst_port}/json')
        if isinstance(path, dict):
            path = path.get('route', path.get('path', []))
        if not isinstance(path, list) or len(path) < 2 or len(path) % 2:
            raise RuntimeError(f'Ruta inválida/no disponible: {path}')
        hops = []
        for i in range(0, len(path), 2):
            entry, exit_ = path[i], path[i + 1]
            sw = entry.get('switch') or entry.get('switchDPID')
            sw2 = exit_.get('switch') or exit_.get('switchDPID')
            if sw != sw2:
                raise RuntimeError('Ruta inconsistente: dos extremos del salto pertenecen a switches distintos')
            hops.append((str(sw), int(entry['port']['portNumber']), int(exit_['port']['portNumber'])))
    return hops, dst_mac


def authorized(student, server, service):
    return any(c.state.upper() == 'ACTIVE' and student.name in c.students and
               any(s.get('name') == server.name and service in s.get('allowed_services', [])
                   for s in c.servers) for c in courses_db)


def flow(switch, name, match, out_port):
    return {'switch': switch, 'name': name, 'cookie': '0', 'priority': '200',
            'active': 'true', **match, 'actions': f'output={out_port}'}


def build_route(hops, student_mac, server_mac, server_ip, service, handler):
    """Construye reglas en ambos sentidos para cada salto y ARP bidireccional."""
    proto = '6' if service.protocol == 'tcp' else '17' if service.protocol == 'udp' else None
    if proto is None:
        raise ValueError('Solo se admiten servicios TCP/UDP')
    flows = []
    for idx, (sw, incoming, outgoing) in enumerate(hops):
        common = f'{handler}-{idx}'
        flows.append(flow(sw, f'{common}-fwd', {
            'in_port': str(incoming), 'eth_type': '0x0800', 'eth_src': student_mac,
            'eth_dst': server_mac, 'ipv4_dst': server_ip, 'ip_proto': proto,
            'tp_dst': str(service.port)}, outgoing))
        flows.append(flow(sw, f'{common}-rev', {
            'in_port': str(outgoing), 'eth_type': '0x0800', 'eth_src': server_mac,
            'eth_dst': student_mac, 'ip_proto': proto, 'tp_src': str(service.port)}, incoming))
        flows.append(flow(sw, f'{common}-arp-fwd', {
            'in_port': str(incoming), 'eth_type': '0x0806', 'eth_src': student_mac}, outgoing))
        flows.append(flow(sw, f'{common}-arp-rev', {
            'in_port': str(outgoing), 'eth_type': '0x0806', 'eth_src': server_mac}, incoming))
    return flows


async def push_flows(flows):
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    installed = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for f in flows:
            async with session.post(STATIC_URL, json=f) as response:
                body = await response.text()
                if response.status >= 400 or ('error' in body.lower() and 'no error' not in body.lower()):
                    raise RuntimeError(f'Falló flow {f["name"]}: HTTP {response.status}: {body}')
                installed.append(f['name'])
    return installed


def delete_flows(names):
    errors = []
    for name in names:
        try:
            r = requests.delete(STATIC_URL, json={'name': name}, timeout=TIMEOUT)
            r.raise_for_status()
            if 'error' in r.text.lower() and 'no error' not in r.text.lower():
                raise RuntimeError(r.text)
        except Exception as e:
            errors.append(f'{name}: {e}')
    return errors


def pick(items, name):
    return next((x for x in items if x.name.lower() == name.strip().lower()), None)


def menu_courses():
    print('\n--- CURSOS ---\n1) Listar\n2) Mostrar detalles\n3) Gestionar alumnos')
    choice = input('>>> ').strip()
    if choice == '1':
        for c in courses_db:
            print(f'{c.code} | {c.name} | {c.state}')
    elif choice == '2':
        c = pick(courses_db, input('Nombre del curso: '))
        if c:
            print(f'Código: {c.code}\nEstado: {c.state}\nAlumnos: {", ".join(c.students)}')
            for s in c.servers:
                print(f'Servidor: {s["name"]}; servicios permitidos: {", ".join(s.get("allowed_services", []))}')
        else:
            print('Curso no encontrado')
    elif choice == '3':
        c = pick(courses_db, input('Nombre del curso: '))
        if not c:
            print('Curso no encontrado'); return
        op = input('1) Agregar  2) Quitar: ').strip()
        student = pick(students_db, input('Nombre del alumno: '))
        if not student:
            print('El alumno debe estar registrado'); return
        if op == '1':
            if student.name not in c.students:
                c.students.append(student.name)
            print('Alumno agregado')
        elif op == '2':
            if student.name in c.students:
                c.students.remove(student.name)
                print('Alumno retirado')
            else:
                print('No estaba matriculado')


def menu_students():
    print('\n--- ALUMNOS ---\n1) Crear\n2) Listar\n3) Mostrar detalles')
    op = input('>>> ').strip()
    if op == '1':
        name = input('Nombre: ').strip()
        mac = input('MAC: ').strip().lower()
        if not name or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac):
            print('Nombre o MAC inválidos'); return
        if pick(students_db, name):
            print('Alumno ya registrado'); return
        students_db.append(Student(name, mac))
        print('Alumno registrado')
    elif op == '2':
        for s in students_db:
            print(f'{s.name} | MAC: {s.mac}')
    elif op == '3':
        s = pick(students_db, input('Nombre: '))
        print(f'{s.name} | MAC: {s.mac}' if s else 'Alumno no encontrado')


def menu_servers():
    print('\n--- SERVIDORES ---\n1) Listar\n2) Mostrar servicios')
    op = input('>>> ').strip()
    if op == '1':
        for s in servers_db:
            print(f'{s.name} | IP: {s.ip}')
    elif op == '2':
        s = pick(servers_db, input('Servidor: '))
        if s:
            print(f'{s.name} | IP: {s.ip}')
            for v in s.services.values():
                print(f'{v.name}: {v.protocol.upper()} / {v.port}')
        else:
            print('Servidor no encontrado')


def create_connection():
    student = pick(students_db, input('Nombre del alumno: '))
    server = pick(servers_db, input('Nombre del servidor: '))
    service_name = input('Servicio (ssh/web/dev): ').strip().lower()
    if not student or not server or service_name not in server.services:
        print('Alumno, servidor o servicio inexistente'); return
    if not authorized(student, server, service_name):
        print('ERROR: alumno no autorizado para ese servicio'); return
    handler = 'h_' + uuid.uuid4().hex[:10]
    flows = []
    try:
        hops, server_mac = calculate_route(student.mac, server.ip)
        flows = build_route(hops, student.mac, server_mac, server.ip,
                            server.services[service_name], handler)
        asyncio.run(push_flows(flows))
    except Exception as e:
        if flows:
            delete_flows([f['name'] for f in flows])
        print(f'ERROR: no se pudo instalar la ruta: {e}')
        return
    connections_db[handler] = {'student': student.name, 'server': server.name,
                               'service': service_name, 'hops': hops,
                               'flows': [f['name'] for f in flows]}
    print(f'Conexión creada: {handler}; {len(flows)} reglas enviadas')
    print('Verifica su instalación real con ovs-ofctl dump-flows en cada switch.')


def menu_connections():
    print('\n--- CONEXIONES ---\n1) Crear\n2) Listar\n3) Mostrar ruta\n4) Borrar')
    op = input('>>> ').strip()
    if op == '1':
        create_connection()
    elif op == '2':
        for h, d in connections_db.items():
            print(f'{h}: {d["student"]} -> {d["server"]} ({d["service"]})')
    elif op == '3':
        h = input('Handler: ').strip()
        d = connections_db.get(h)
        print(json.dumps(d, indent=2) if d else 'Handler no encontrado')
    elif op == '4':
        h = input('Handler: ').strip()
        d = connections_db.get(h)
        if not d:
            print('Handler no encontrado'); return
        student = pick(students_db, d['student'])
        server = pick(servers_db, d['server'])
        if not student or not server or not authorized(student, server, d['service']):
            print('ERROR: alumno no autorizado actualmente; eliminación denegada por la política del laboratorio')
            return
        errors = delete_flows(d['flows'])
        if errors:
            print('Errores al eliminar:\n' + '\n'.join(errors)); return
        del connections_db[h]
        print('Conexión eliminada; comprueba los flows en OvS')


def main():
    global students_db, servers_db, courses_db
    try:
        students_db, servers_db, courses_db = load_data()
    except Exception as e:
        print(f'Error leyendo database.yaml: {e}')
        return
    while True:
        print('\n=== Network Policy Manager TEL354 ===')
        print('1) Importar\n2) Exportar\n3) Cursos\n4) Alumnos\n5) Servidores\n6) Políticas\n7) Conexiones\n8) Salir')
        op = input('>>> ').strip()
        if op == '3': menu_courses()
        elif op == '4': menu_students()
        elif op == '5': menu_servers()
        elif op == '7': menu_connections()
        elif op == '8': break
        elif op in ('1', '2', '6'):
            print('Opción no implementada: la guía no la marca con (*)')
        else:
            print('Opción inválida')


if __name__ == '__main__':
>>>>>>> c13720a (Correcion - Lab 4)
    main()

