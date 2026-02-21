"""Utilidades para el modulo ERP."""

from decimal import Decimal


def numero_a_letra(numero: Decimal) -> str:
    """Convierte un monto a su representacion en letras para TotalLetra.

    Formato PROD: "( MONTO PESOS XX/100 M.N. )"
    Ejemplo: 11236.83 -> "( ONCE MIL DOSCIENTOS TREINTA Y SEIS PESOS 83/100 M.N. )"
    """
    UNIDADES = ['', 'UN', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE']
    DECENAS = ['', 'DIEZ', 'VEINTE', 'TREINTA', 'CUARENTA', 'CINCUENTA',
               'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA']
    ESPECIALES = {
        11: 'ONCE', 12: 'DOCE', 13: 'TRECE', 14: 'CATORCE', 15: 'QUINCE',
        16: 'DIECISEIS', 17: 'DIECISIETE', 18: 'DIECIOCHO', 19: 'DIECINUEVE',
        21: 'VEINTIUNO', 22: 'VEINTIDOS', 23: 'VEINTITRES', 24: 'VEINTICUATRO',
        25: 'VEINTICINCO', 26: 'VEINTISEIS', 27: 'VEINTISIETE', 28: 'VEINTIOCHO', 29: 'VEINTINUEVE'
    }
    CENTENAS = ['', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS',
                'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS']

    def convertir_grupo(n: int) -> str:
        if n == 0:
            return ''
        if n == 100:
            return 'CIEN'

        resultado = ''
        centenas = n // 100
        resto = n % 100
        decenas = resto // 10
        unidades = resto % 10

        if centenas > 0:
            resultado += CENTENAS[centenas]

        if resto > 0:
            if centenas > 0:
                resultado += ' '

            if resto in ESPECIALES:
                resultado += ESPECIALES[resto]
            elif decenas > 0:
                resultado += DECENAS[decenas]
                if unidades > 0:
                    resultado += ' Y ' + UNIDADES[unidades]
            else:
                resultado += UNIDADES[unidades]

        return resultado

    try:
        numero = Decimal(str(numero))
        entero = int(numero)
        centavos = int(round((numero - entero) * 100))

        if entero == 0:
            letra = 'CERO'
        elif entero == 1:
            letra = 'UN'
        else:
            letra = ''
            millones = entero // 1000000
            miles = (entero % 1000000) // 1000
            unidades_val = entero % 1000

            if millones > 0:
                if millones == 1:
                    letra += 'UN MILLON'
                else:
                    letra += convertir_grupo(millones) + ' MILLONES'

            if miles > 0:
                if letra:
                    letra += ' '
                if miles == 1:
                    letra += 'UN MIL'
                else:
                    letra += convertir_grupo(miles) + ' MIL'

            if unidades_val > 0:
                if letra:
                    letra += ' '
                letra += convertir_grupo(unidades_val)

        return f"( {letra} PESOS {centavos:02d}/100 M.N. )"
    except Exception:
        return ''
