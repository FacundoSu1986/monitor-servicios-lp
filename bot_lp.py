import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import os

def scraping_la_plata():
    url = "https://www.0221.com.ar/seccion/la-ciudad"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        titulares = soup.find_all('h2')
        
        hallazgos = []
        palabras_alerta = ["corte", "luz", "agua", "choque", "incendio", "edelap", "absa", "inundación", "tránsito", "robo", "secuestro", "persecución", "tiroteo", "motochorro", "asalto", "policía"]
        
        for t in titulares:
            texto = t.get_text().lower()
            if any(palabra in texto for palabra in palabras_alerta):
                hallazgos.append({
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "evento": t.get_text().strip()
                })
        
        if hallazgos:
            df = pd.DataFrame(hallazgos)
            archivo = 'historial_lp.csv'
            if not os.path.isfile(archivo):
                df.to_csv(archivo, index=False)
            else:
                df.to_csv(archivo, mode='a', header=False, index=False)
            print(f"Éxito: {len(hallazgos)} eventos guardados.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    scraping_la_plata()
