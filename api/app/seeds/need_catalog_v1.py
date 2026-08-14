"""Catálogo de necesidades v1 — BORRADOR.

⚠️ Supuesto de Fase 0 (§1.5 del alcance): el catálogo inicial debe
acordarse con la operación humanitaria; este borrador permite desarrollar
y probar, y se versiona (v2, v3…) cuando el aliado lo valide. No debe
inventarse solo por desarrollo — está marcado como draft_v1.

Formato: (code, nombre_es, unidad, horizonte_default, padre)
Unidades: UNIT (unidad), KIT, PERSON_DAY, KG, L, M2, SERVICE, TRIP, HOUR
Horizontes: EMERGENCY, RECOVERY, RECONSTRUCTION
"""

CATALOG_VERSION = 1

# Categorías raíz
ROOTS = [
    ("WATER", "Agua e hidratación"),
    ("FOOD", "Alimentación"),
    ("SHELTER", "Alojamiento y abrigo"),
    ("HEALTH", "Salud"),
    ("HYGIENE", "Higiene y saneamiento"),
    ("CLOTHING", "Ropa y calzado"),
    ("HOUSING", "Vivienda y reparaciones"),
    ("EDUCATION", "Educación"),
    ("LIVELIHOOD", "Medios de vida"),
    ("PSYCHOSOCIAL", "Apoyo psicosocial"),
    ("TRANSPORT", "Transporte y logística"),
    ("SERVICES", "Servicios profesionales"),
]

ITEMS = [
    # (code, nombre, unidad, horizonte, padre)
    ("WATER.BOTTLED", "Agua potable embotellada", "L", "EMERGENCY", "WATER"),
    ("WATER.TANK", "Tanque de almacenamiento de agua", "UNIT", "RECOVERY", "WATER"),
    ("WATER.FILTER", "Filtro purificador de agua", "UNIT", "RECOVERY", "WATER"),
    ("FOOD.RATION", "Ración alimentaria familiar", "KIT", "EMERGENCY", "FOOD"),
    ("FOOD.INFANT", "Alimentación infantil / fórmula", "KIT", "EMERGENCY", "FOOD"),
    ("FOOD.HOT_MEAL", "Comida caliente servida", "UNIT", "EMERGENCY", "FOOD"),
    ("SHELTER.TENT", "Carpa / alojamiento temporal", "UNIT", "EMERGENCY", "SHELTER"),
    ("SHELTER.MATTRESS", "Colchón o colchoneta", "UNIT", "EMERGENCY", "SHELTER"),
    ("SHELTER.BLANKET", "Cobija / frazada", "UNIT", "EMERGENCY", "SHELTER"),
    ("SHELTER.KIT", "Kit de alojamiento (carpa+colchón+cobijas)", "KIT", "EMERGENCY", "SHELTER"),
    ("HEALTH.FIRST_AID", "Kit de primeros auxilios", "KIT", "EMERGENCY", "HEALTH"),
    ("HEALTH.MEDICATION", "Medicamentos con fórmula", "SERVICE", "EMERGENCY", "HEALTH"),
    ("HEALTH.MEDICAL_ATTENTION", "Atención médica", "SERVICE", "EMERGENCY", "HEALTH"),
    ("HEALTH.MOBILITY_AID", "Ayudas de movilidad (silla, muletas)", "UNIT", "RECOVERY", "HEALTH"),
    ("HYGIENE.KIT", "Kit de aseo personal", "KIT", "EMERGENCY", "HYGIENE"),
    ("HYGIENE.DIAPERS", "Pañales (bebé/adulto)", "UNIT", "EMERGENCY", "HYGIENE"),
    ("HYGIENE.FEMININE", "Kit de higiene menstrual", "KIT", "EMERGENCY", "HYGIENE"),
    ("HYGIENE.LATRINE", "Solución sanitaria temporal", "UNIT", "RECOVERY", "HYGIENE"),
    ("CLOTHING.ADULT", "Ropa adulto", "KIT", "EMERGENCY", "CLOTHING"),
    ("CLOTHING.CHILD", "Ropa infantil", "KIT", "EMERGENCY", "CLOTHING"),
    ("CLOTHING.FOOTWEAR", "Calzado", "UNIT", "EMERGENCY", "CLOTHING"),
    ("HOUSING.TARP", "Plástico / lona para techo", "M2", "EMERGENCY", "HOUSING"),
    ("HOUSING.ROOF.REPAIR", "Reparación de techo", "SERVICE", "RECOVERY", "HOUSING"),
    ("HOUSING.WALL.REPAIR", "Reparación de muros", "SERVICE", "RECOVERY", "HOUSING"),
    ("HOUSING.MATERIALS", "Materiales de construcción", "KIT", "RECOVERY", "HOUSING"),
    ("HOUSING.REBUILD", "Reconstrucción de vivienda", "SERVICE", "RECONSTRUCTION", "HOUSING"),
    ("HOUSING.RENT_SUBSIDY", "Subsidio de arriendo temporal", "SERVICE", "RECOVERY", "HOUSING"),
    ("EDUCATION.KIT", "Kit escolar", "KIT", "RECOVERY", "EDUCATION"),
    ("EDUCATION.SPACE", "Espacio educativo temporal", "SERVICE", "RECOVERY", "EDUCATION"),
    ("LIVELIHOOD.TOOLS", "Herramientas de trabajo", "KIT", "RECOVERY", "LIVELIHOOD"),
    ("LIVELIHOOD.SEED_CAPITAL", "Capital semilla / reactivación", "SERVICE", "RECONSTRUCTION",
     "LIVELIHOOD"),
    ("LIVELIHOOD.AGRICULTURE", "Insumos agropecuarios", "KIT", "RECOVERY", "LIVELIHOOD"),
    ("PSYCHOSOCIAL.SUPPORT", "Acompañamiento psicosocial", "PERSON_DAY", "RECOVERY",
     "PSYCHOSOCIAL"),
    ("TRANSPORT.CARGO", "Transporte de carga", "TRIP", "EMERGENCY", "TRANSPORT"),
    ("TRANSPORT.PEOPLE", "Transporte de personas", "TRIP", "EMERGENCY", "TRANSPORT"),
    ("SERVICES.ENGINEERING", "Evaluación estructural / ingeniería", "SERVICE", "RECOVERY",
     "SERVICES"),
    ("SERVICES.LEGAL", "Asesoría jurídica", "SERVICE", "RECOVERY", "SERVICES"),
    ("SERVICES.DEBRIS", "Remoción de escombros", "SERVICE", "EMERGENCY", "SERVICES"),
]
