# from src.config import (
#     IRIDIUM_EMOJI,
#     GOLD_EMOJI,
#     SILVER_EMOJI,
#     HEALTH_EMOJI,
#     ENERGY_EMOJI,
#     COIN_EMOJI,
#     POISON_EMOJI,
#     IRIDIUM_ENERGY_EMOJI,
#     GOLD_ENERGY_EMOJI,
#     SILVER_ENERGY_EMOJI,
#     IRIDIUM_HEALTH_EMOJI,
#     GOLD_HEALTH_EMOJI,
#     SILVER_HEALTH_EMOJI,
#     IRIDIUM_POISON_EMOJI,
#     GOLD_POISON_EMOJI,
#     SILVER_POISON_EMOJI
# )
from utils import emojidict
def getQualityFromPath(path):
    if path.endswith('Iridium_Quality.png'):
        return emojidict.get("iridium")
    elif path.endswith('Gold_Quality.png'):
        return emojidict.get("gold")
    elif path.endswith('Silver_Quality.png'):
        return emojidict.get("silver")

    return None

def getHealthEnergyPoisonFromPath(path):
    if path.endswith('Health.png'):
        return emojidict.get("20pxHealth")
    elif path.endswith('Energy.png'):
        return emojidict.get("20pxEnergy")
    elif path.endswith('Poison.png'):
        return emojidict.get("POISON")

    return None

def checkIfShouldBeGoldCoin(foreimages, path=None):
    if not path:
        path = foreimages[0].find_all('img')[0]['src']

    if path.endswith('Gold_Quality_Icon.png'):
        return emojidict.get("coin")

    return None

def qualityHealthEnergyPoison(back_path, foreimages):
    imgs = foreimages[0].find_all('img')

    if not len(imgs) > 0:
        return None

    fore_path = imgs[0]['src']

    # print(f'fore_path: {fore_path}')
    # print(f'back_path: {back_path}')

    if fore_path.endswith('Silver_Quality_Icon.png'):
        if back_path.endswith('Health.png'):
            return emojidict.get("SILVER_HEALTH")
        elif back_path.endswith('Energy.png'):
            return emojidict.get("SILVER_ENERGY")
        elif back_path.endswith('Poison.png'):
            return emojidict.get("SILVER_POISON")
    elif fore_path.endswith('Gold_Quality_Icon.png'):
        if back_path.endswith('Health.png'):
            return emojidict.get("GOLD_HEALTH")
        elif back_path.endswith('Energy.png'):
            return emojidict.get("GOLD_ENERGY")
        elif back_path.endswith('Poison.png'):
            return emojidict.get("GOLD_POISON")
    elif fore_path.endswith('Iridium_Quality_Icon.png'):
        if back_path.endswith('Health.png'):
            return emojidict.get("IRIDIUM_HEALTH")
        elif back_path.endswith('Energy.png'):
            return emojidict.get("IRIDIUM_ENERGY")
        elif back_path.endswith('Poison.png'):
            return emojidict.get("IRIDIUM_POISON")

    return None

def identify(str, pagename=None, foreimages=None, backimage=None):
    if q := getQualityFromPath(str):
        return q
    elif (q := qualityHealthEnergyPoison(str, foreimages)) and len(foreimages) > 0:
        return q
    elif h := getHealthEnergyPoisonFromPath(str):
        return h
    elif (g := checkIfShouldBeGoldCoin(foreimages)) and len(foreimages) > 0 and pagename:
        return g

    return None
