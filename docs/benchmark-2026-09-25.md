# Benchmark de modelos — 20260925-154129
Suite: 5 pruebas por modelo.
Timeout por prueba: 180s.

## Resumen

| Modelo | T1 | T2 | T3 | T4 | T5 | Total | Tiempo |
|---|---|---|---|---|---|---|---|
| qwen3:1.7b | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 | 18.7s |
| qwen3.6:27b | ✅ | ❌ | ❌ | ❌ | ❌ | 1/5 | 199.1s |
| mistral-small3.2:latest | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 | 281.9s |
| gpt-oss:20b | ✅ | ✅ | ⚠️ | ✅ | ✅ | 4/5 | 213.6s |
| muse-glimmer:latest | ⚠️ | ✅ | ✅ | ✅ | ✅ | 4/5 | 402.9s |
| ministral-3:latest | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 | 54.6s |
| ornith-1.5:9b | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 | 58.2s |


## Detalle

### qwen3:1.7b

**tool_simple** — OK — 2.98s

```json
[
  {
    "id": "call_qw0r2xzl",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "path": "hola.txt",
        "content": "hola mundo"
      }
    }
  }
]
```

**tool_selectivo** — OK — 2.33s

```json
[
  {
    "id": "call_fp7a231x",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": "/"
      }
    }
  }
]
```

**razonamiento** — OK — 5.13s

```
El cielo es azul debido a la dispersión de Rayleigh, donde los fotones de luz de color azul (con menor longitud de onda) se dispersan más intensamente en la atmósfera, mientras que los de color rojo (con mayor longitud de onda) se dispersan menos. Esta dispersión hace que la luz azul sea más visible a distancia, dando el efecto del cielo azul.
```

**codigo** — OK — 5.73s

```
```python
def sum_even_numbers(numbers: list[int]) -> int:
    sum_even = 0
    for num in numbers:
        if num % 2 == 0:
            sum_even += num
    return sum_even
```
```

**negativa** — OK — 2.54s

```
La fotosintesis es el proceso mediante el cual las plantas convierten la energía solar en energía chimica para crear glucosa y oxígeno.
```

### qwen3.6:27b

**tool_simple** — OK — 111.36s

```json
[
  {
    "id": "call_9uo5itbh",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "path": "hola.txt",
        "content": "hola mundo"
      }
    }
  }
]
```

**tool_selectivo** — FALLO — 59.27s


**razonamiento** — FALLO — 9.53s


**codigo** — FALLO — 8.10s


**negativa** — FALLO — 10.80s


### mistral-small3.2:latest

**tool_simple** — OK — 63.23s

```json
[
  {
    "id": "call_7z3zce71",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "path": "hola.txt",
        "content": "hola mundo"
      }
    }
  }
]
```

**tool_selectivo** — OK — 48.10s

```json
[
  {
    "id": "call_yh4l3byw",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": "/"
      }
    }
  }
]
```

**razonamiento** — OK — 57.62s

```
El cielo es azul debido a la dispersión de Rayleigh, que ocurre cuando la luz del sol choca con las moléculas de la atmósfera y se dispersa más en longitudes de onda cortas, como el azul. Nuestros ojos perciben esta luz dispersa como el color azul dominante del cielo.
```

**codigo** — OK — 54.85s

```
```python
def suma_pares(numeros):
    return sum(num for num in numeros if num % 2 == 0)
```
```

**negativa** — OK — 58.08s

```
La fotosíntesis es el proceso mediante el cual las plantas, algas y algunas bacterias convierten la luz solar, el dióxido de carbono y el agua en glucosa y oxígeno.
```

### gpt-oss:20b

**tool_simple** — OK — 55.74s

```json
[
  {
    "id": "call_ftgt6nop",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "content": "hola mundo",
        "path": "hola.txt"
      }
    }
  }
]
```

**tool_selectivo** — OK — 37.97s

```json
[
  {
    "id": "call_00q38ova",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": "."
      }
    }
  }
]
```

**razonamiento** — ERROR — 79.70s

```
Server error '500 Internal Server Error' for url 'http://localhost:11434/api/chat'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
```

**codigo** — OK — 21.84s

```
```python
def suma_pares(lista):
    return sum(x for x in lista if x % 2 == 0)
```
```

**negativa** — OK — 18.35s

```
La fotosíntesis es el proceso mediante el cual las plantas, algas y algunas bacterias convierten la luz solar, el dióxido de carbono y el agua en glucosa y oxígeno.
```

### muse-glimmer:latest

**tool_simple** — ERROR — 52.18s

```
Server error '500 Internal Server Error' for url 'http://localhost:11434/api/chat'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500
```

**tool_selectivo** — OK — 86.95s

```json
[
  {
    "id": "call_h91mvtg5",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": "/"
      }
    }
  }
]
```

**razonamiento** — OK — 129.76s

```
El cielo es azul porque la atmósfera terrestre dispersa la luz solar y las longitudes de onda cortas se esparcen mucho más que las largas. Este fenómeno es la dispersión de Rayleigh, que hace que percibamos predominantemente el color azul durante el día.
```

**codigo** — OK — 61.88s

```
```python
def suma_pares(numeros):
    return sum(n for n in numeros if n % 2 == 0)
```
```

**negativa** — OK — 72.13s

```
La fotosíntesis es el proceso mediante el cual las plantas, algas y algunas bacterias usan la luz solar para convertir dióxido de carbono y agua en glucosa y oxígeno.
```

### ministral-3:latest

**tool_simple** — OK — 23.98s

```json
[
  {
    "id": "call_yhidwkhk",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "path": "hola.txt",
        "content": "hola mundo"
      }
    }
  }
]
```

**tool_selectivo** — OK — 5.94s

```json
[
  {
    "id": "call_29gylq6f",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": ""
      }
    }
  }
]
```

**razonamiento** — OK — 10.02s

```
El cielo aparece azul debido a la **dispersión de Rayleigh**, un fenómeno en el que la luz solar (compuesta por todos los colores) choca con las moléculas de la atmósfera y se dispersa más eficientemente en las longitudes de onda cortas (como el azul y el violeta), que son las que percibimos predominantemente.

Aunque el violeta se dispersa aún más, nuestros ojos son menos sensibles a él y el azul
```

**codigo** — OK — 6.44s

```
```python
def suma_pares(numeros):
    return sum(num for num in numeros if num % 2 == 0)
```
```

**negativa** — OK — 8.26s

```
La **fotosíntesis** es el proceso mediante el cual las plantas, algas y algunas bacterias convierten la energía de la luz solar, dióxido de carbono y agua en glucosa (azúcar) y oxígeno, utilizando clorofila como pigmento clave.
```

### ornith-1.5:9b

**tool_simple** — OK — 26.02s

```json
[
  {
    "id": "CulHl6D4iA9RClq1KLmCU2CP6SQdahmo",
    "function": {
      "index": 0,
      "name": "crear_archivo",
      "arguments": {
        "path": "hola.txt",
        "content": "hola mundo"
      }
    }
  }
]
```

**tool_selectivo** — OK — 7.47s

```json
[
  {
    "id": "Cvwp5LSdQKAIEk6TiGhGV7MDzPiz06tO",
    "function": {
      "index": 0,
      "name": "listar_carpeta",
      "arguments": {
        "path": "."
      }
    }
  }
]
```

**razonamiento** — OK — 11.04s

```
El cielo es azul porque la luz del Sol, al atravesar la atmósfera, choca con las moléculas de aire y se dispersa en todas direcciones. Esta dispersión, conocida como **dispersión de Rayleigh**, afecta más a las longitudes de onda cortas (el azul y el violeta), por lo que esos colores se esparcen mucho más que el rojo y dominan el color que vemos.
```

**codigo** — OK — 5.65s

```
```python
def suma_pares(numeros):
    return sum(n for n in numeros if n % 2 == 0)
```
```

**negativa** — OK — 8.05s

```
La fotosíntesis es el proceso mediante el cual las plantas, algas y algunas bacterias convierten la luz solar, el agua y el dióxido de carbono en glucosa y oxígeno.
```
