import json
import os

# el de negocios me pidio un excel con los endpoints para el manual de usuario
# no se como exportar a excel con python asi que imprimo esto y el lo copia y pega

path_json = "../openapi.json"

if not os.path.exists(path_json):
    print("Falta el openapi.json. Tienes que entrar a /docs, copiar todo el json y guardarlo aqui manual")
    exit()

with open(path_json, "r", encoding="utf-8") as f:
    data = json.load(f)

print("--- RUTAS DEL BANCO ---")
rutas = data.get("paths", {})
for ruta in rutas.keys():
    metodos = list(rutas[ruta].keys())
    print(f"URL: {ruta} | Metodos: {metodos}")
    
# TODO: automatizar esto con cron