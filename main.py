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

students_db = []
servers_db = []
courses_db = []
connections_db = {}


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
    main()
