"""English-keyed UI catalogues, included by normal Python packaging."""

from ..translations import ZH_CN
from .es import MESSAGES as ES
from .pt_BR import MESSAGES as PT_BR
from .fr import MESSAGES as FR
from .ja import MESSAGES as JA

CATALOGUES = {"zh_CN": ZH_CN, "es": ES, "pt_BR": PT_BR, "fr": FR, "ja": JA}

# Standard Qt confirmation buttons. Ampersands mark keyboard mnemonics.
DIALOG_BUTTONS = {
    "zh_CN": ("是", "否", "取消", "确定", "关闭"),
    "es": ("Sí", "No", "Cancelar", "Aceptar", "Cerrar"),
    "pt_BR": ("Sim", "Não", "Cancelar", "OK", "Fechar"),
    "fr": ("Oui", "Non", "Annuler", "OK", "Fermer"),
    "ja": ("はい", "いいえ", "キャンセル", "OK", "閉じる"),
}
