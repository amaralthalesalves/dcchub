"""Moderação automática de comentários do DCCHub.

Objetivo: o mínimo possível de trabalho humano. Cada comentário recebe uma de três decisões:

  publish  limpo: publica direto;
  reject   claramente fora das regras (palavrão, xingamento, aparência, vida pessoal, ameaça, dado pessoal…):
           NÃO é guardado; quem escreveu recebe o motivo e reescreve na hora (o formulário avisa enquanto digita);
  hold     ambíguo de verdade (ex.: acusação de machismo/racismo, que pode ser crítica legítima ou difamação):
           vai para a fila de moderação.

Precisão importa nos dois sentidos: falso positivo vira atrito para quem escreve, falso negativo vira ataque
publicado. Por isso: normalização forte (acentos, leet, letras separadas, coringas, letras repetidas,
aumentativo/diminutivo), chave fonética para erros de grafia e regras de CONTEXTO para palavras ambíguas
("velho", "lixo", "burro", "fede"… só contam quando ditas sobre a pessoa: "ele é burro", não "algoritmo burro").
Avaliado contra o dicionário pt_BR inteiro e a lista das 50 mil palavras mais usadas (ver tests/).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PUBLISH, HOLD, REJECT = "publish", "hold", "reject"

MESSAGES = {
    "palavrao": "Tire os palavrões — a crítica fica mais forte sem eles.",
    "xingamento": "Critique a aula, não a pessoa: troque o xingamento por um exemplo concreto do que aconteceu.",
    "odio": "Comentários discriminatórios (gênero, raça, orientação, origem, deficiência…) não são aceitos.",
    "ameaca": "Ameaças ou desejos de mal a alguém não são aceitos.",
    "aparencia": "Não comente aparência, corpo, cheiro ou idade de ninguém — fale da disciplina e das aulas.",
    "vida_pessoal": "Deixe a vida pessoal de fora (relacionamentos, religião, política, bebida…): fale das práticas de ensino.",
    "denuncia": "Denúncias graves (assédio, abuso, crimes) não devem ser feitas aqui: procure a Ouvidoria da UFMG ou o DCE, "
                "que têm canais sigilosos e podem agir de verdade.",
    "boato": "Fale só do que você viveu na disciplina, não de boatos ou do que \"dizem\".",
    "dado_pessoal": "Remova dados pessoais (telefone, CPF, matrícula, endereço, redes sociais, e-mail pessoal).",
    "link": "Remova o link (encurtadores e muitos links não são aceitos).",
    "maiusculas": "Escreva sem caixa alta.",
    "sem_conteudo": "O comentário parece não ter conteúdo sobre a disciplina.",
    "discriminacao": "Acusação de discriminação: revisada por uma pessoa antes de publicar.",
}
ACTION = {k: REJECT for k in MESSAGES} | {"discriminacao": HOLD}


@dataclass(frozen=True)
class Issue:
    category: str
    evidence: str

    @property
    def action(self) -> str:
        return ACTION[self.category]

    @property
    def message(self) -> str:
        return MESSAGES[self.category]


# --------------------------------------------------------------------------- léxico
# Sempre fora (em qualquer contexto)
PROFANITY = """porra caralho krl crl caraio carai merda bosta puta puto putaria putinha foda fodase foder fodido fodida fudido fudida
fuder fudeu fodeu fodam cacete cu cuzao cuzona arrombado arrombada pqp vsf vtnc tnc fdp buceta xoxota piroca punheta siririca
caralhudo bostinha""".split()
INSULTS = """idiota imbecil otario otaria babaca escroto escrota vagabundo vagabunda cretino cretina canalha energumeno jumento asno
verme escoria nojento nojenta estupido estupida demente panaca pilantra safado safada desgracado desgracada corno piranha vadia
debiloide imprestavel""".split()
SLURS = """viado viadinho veado bicha boiola sapatao traveco crioulo retardado retardada mongoloide mongol aleijado""".split()
# Só quando ditas SOBRE A PESSOA ("ele é um burro", "professor velho", "esse gordo"); senão são palavras comuns
DIRECTED = {
    "xingamento": """burro burra anta besta palhaco palhaca lixo inutil ridiculo ridicula porco porca animal monstro psicopata
                     doente louco louca maluco maluca idiotinha otaria imbecilzinho""".split(),
    "aparencia": """velho velha feio feia fede fedido fedida fedorento fedorenta gordo gorda baixinho baixinha careca magrelo
                    magrela banguela esquisito esquisita velhote coroa""".split(),
    "odio": """macaco macaca nazista""".split(),
}
# Termos de aparência/idade inequívocos (sempre)
APPEARANCE = """gaga caquetico caquetica senil obeso obesa""".split()
# Grafias erradas/variações conhecidas (a fonética exata pega "imbessil"; estas ela não alcança)
MISSPELLINGS = {
    "kralho": "caralho", "karalho": "caralho", "caralio": "caralho", "caraleo": "caralho", "karai": "caralho", "karaio": "caralho",
    "krai": "caralho", "poha": "porra", "porraa": "porra", "pora": "porra", "mrd": "merda", "merd": "merda", "bost": "bosta",
    "fdps": "fdp", "fodasse": "fodase", "fodace": "fodase", "fds": "fodase", "fuck": "foda", "fucking": "foda", "shit": "merda",
    "inbecil": "imbecil", "imbecio": "imbecil", "idiotra": "idiota", "otaro": "otario", "estupidu": "estupido",
    "cuzaum": "cuzao", "kct": "cacete", "pqp": "pqp", "vsf": "vsf", "sapatona": "sapatao", "viadao": "viado", "bixa": "bicha",
}

# Expressões (texto normalizado, com espaços simples)
PHRASES = {
    "palavrao": [r"filho da puta", r"vai se f[ou]d[ea]r?", r"tomar no c[uo]", r"vai a merda", r"pau no cu", r"puta que pariu",
                 r"do caralho"],
    "aparencia": [r"mau halito", r"cheir(?:o|a) (?:mal|ruim|de suor|a suor)",
                  r"\bfed(?:e|ia|endo) (?:a |de )?(?:cigarro|suor|cachaca|bebida|alcool|cebola|sovaco|cecê|cc|catinga|mofo|esgoto)\b",
                  r"\bcheir(?:o|a|ando) (?:a |de )(?:cigarro|cachaca|bebida|alcool|sovaco|catinga)\b", r"(?:ja )?(?:devia|deveria|precisa|tem que) (?:ter )?se aposentad?r?",
                  r"idade avancada", r"(?:muito|mt|mto|bem) velh[oa] pra", r"cara de (?:sono|bunda|cu|pau)", r"se veste (?:mal|feio)",
                  r"roupa(?:s)? (?:feia|ridicula|brega)"],
    "vida_pessoal": [r"(?:esposa|marido|amante|namorad[oa]|ex[ -]mulher|ex[ -]marido) del[ea]", r"\btra(?:i|iu|ia|indo) a (?:esposa|mulher)",
                     r"\btra(?:i|iu|ia|indo) o marido", r"vida pessoal del[ea]", r"\b(?:e|eh) (?:gay|lesbica|bissexual|trans|travesti)\b",
                     r"orientacao sexual", r"\b(?:bebad[oa]|alcoolatra|cachaceir[oa]|drogad[oa]|maconheir[oa]|cheirad[oa])\b",
                     r"\b(?:usa|usava|cheira|cheirava|fuma|fumava) (?:droga|maconha|cocaina|crack)", r"\bvai na igreja\b",
                     r"\b(?:bolsonarista|bolsominion|petista|lulista|esquerdopata|comunista de merda|isentao)\b",
                     r"\b(?:bolsonaro|lula)\b",
                     r"\b(?:e|eh|era) de (?:esquerda|direita)\b", r"\bfiliad[oa] (?:ao|a|no|na) (?:partido|pt|pl|psol)\b", r"\bse divorci", r"\b(?:corno|chifrudo)\b"],
    "denuncia": [r"\bassedi(?:o|a|ou|ava|ador|adora|ando)\b", r"abus(?:o|a|ou|ava|ador)\w* sexual", r"\bestupr\w*", r"\bmolest\w*",
                 r"\bpedofil\w*", r"\b(?:da|deu|dava|dando|dar) em cima d(?:e|a|as|os) alun", r"\bladr(?:ao|ona)\b",
                 r"\broub(?:a|ou|ava) (?:dinheiro|verba|bolsa)", r"\bcorrupt\w*", r"\bpropina\b", r"\bsuborn\w*", r"\bvend(?:e|ia|eu) nota",
                 r"\b(?:deveria|devia|merece) (?:ser )?pres[oa]\b", r"\bcriminos[oa]\b", r"\bplagi(?:ou|ava|a) (?:de )?alun"],
    "discriminacao": [r"\bracis(?:ta|tas|mo)\b", r"\bmachis(?:ta|tas|mo)\b", r"\bhomofob\w*", r"\btransfob\w*", r"\bmisogin\w*",
                      r"\bxenofob\w*", r"\bpreconceituos[oa]s?\b", r"\bcapacitis\w*", r"\bgordofob\w*", r"\betaris\w*"],
    "ameaca": [r"\b(?:tomara|espero|queria|quero|desejo|torco)? ?que (?:ele|ela|o professor|a professora|esse cara|esse professor|"
               r"essa professora|o cara|esse prof|essa prof) (?:\w+ ){0,2}(?:morra|morresse|se foda|se exploda|sofra|sofresse|"
               r"seja atropelad[oa]|bata o carro|tenha (?:um )?cancer|apanhe|se mate|se matasse|va pro inferno|queime)\b",
               r"\b(?:matar|mato|mataria|socar|esmurrar|espancar|bater n(?:ele|ela)|quebrar a cara|meter a mao) (?:ele|ela|o professor|a professora|esse|essa|o cara|nele|nela|del[ea])\b",
               r"\b(?:dar|levar|meter) (?:um )?(?:tiro|facada|soco|murro|surra) n(?:ele|ela|o professor|a professora)\b",
               r"\b(?:tirar satisfacao|acertar as contas|ir la na casa|pegar (?:ele|ela) na saida|esperar (?:ele|ela) na saida)\b",
               r"\b(?:deveria|devia|merece) morrer\b", r"\bmorre(?:r)? logo\b"],
    "boato": [r"\b(?:dizem|falam|contam|comentam) que\b", r"\bouvi dizer\b", r"\bme (?:disseram|contaram|falaram) que\b",
              r"\bboatos?\b", r"\brumores?\b", r"\bfofoca\b", r"\btodo mundo sabe que\b", r"\bpelo que (?:dizem|falam|contam)\b"],
    "odio": [r"\b(?:mulher|mulheres|gay|gays|negro|negros|negra|negras|preto|pretos|nordestino|nordestinos|baiano|baianos|gordo|gordos|"
             r"velho|velhos|trans|travesti|travestis|estrangeiro|estrangeiros|deficiente|deficientes)\b (?:nao|n|nunca) "
             r"(?:deveria|deveriam|devia|deviam|deve|devem|sabe|sabem|consegue|conseguem|serve|servem|presta|prestam|tem capacidade|"
             r"pode|podem)\b", r"\blugar de mulher\b", r"\bcoisa de (?:mulher|gay|viado|preto)\b"],
}
# Palavras comuns que a normalização/fonética aproximaria do léxico, mas não são ofensa
ALLOWLIST = set("""porrada porre porres porradas putz puts pura puro cucu curso cursos fora toda moda roda poder porta corpo corpos
lido luxo fixo foca bota boto pato idioma idiomas escrita escrito barro casete puxa vadio tcc forum focado focada focar putrido
putrefacao computa computar computacao computador disputa disputar reputacao imputar amputar deputado deputada estupefato
estupefata cacetete merdeiro bostoniano futebol futuro futura fodder anta antas bestas besteira cururu cuca cuidado cuia escrotal
vagabundagem baba babado macacao macacos coroas coroamento coroado coroar cassete cassetes vermos vadios cornaca cornaco
cornacas cornacos porcao porcoes macacoes palha palhas baixo baixa baixos baixas carece best""".split())

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "l"})
MASK = "*#_%"
PERSON_REFS = ["ele", "ela", "professor", "professora", "prof", "profa", "profe", "docente", "cara", "sujeito", "figura",
               "esse cara", "o cara", "esse professor", "essa professora", "esse prof", "essa prof", "o professor", "a professora"]
COPULAS = ["e", "eh", "era", "foi", "parece", "parecia", "virou", "ta", "tava", "esta", "estava", "fica", "ficava", "ficou",
           "continua", "se acha", "se mostrou", "sendo", "seja", "sao"]
DETS = ["um", "uma", "o", "a", "bem", "muito", "mt", "mto", "mo", "meio", "tao", "super", "completamente", "totalmente",
        "extremamente", "mega", "puta", "grande", "baita", "verdadeiro", "verdadeira", "velho", "velha", "pouco"]
DEMONSTRATIVE_OK = {"burro", "burra", "velho", "velha", "velhote", "gordo", "gorda", "palhaco", "palhaca", "porco", "porca",
                    "animal", "maluco", "maluca", "louco", "louca", "macaco", "macaca", "feio", "feia", "careca", "coroa", "anta", "besta"}
INSTITUTIONAL_EMAIL = re.compile(r"@(?:[\w-]+\.)*ufmg\.br$", re.I)
SHORTENERS = re.compile(r"\b(?:bit\.ly|tinyurl\.com|cutt\.ly|t\.co|is\.gd|encurtador\.com\.br|shorturl\.at|rb\.gy)\b", re.I)


# --------------------------------------------------------------------------- normalização
def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def squash(word: str, keep: int = 1) -> str:
    """Repetições de 3+ letras -> 1 ("buuurro" -> "burro"); keep=0 colapsa também as duplas ("idiotaa" -> "idiota")."""
    word = re.sub(r"(.)\1{2,}", r"\1", word)
    return re.sub(r"(.)\1", r"\1", word) if keep == 0 else word


SUFFIXES = ("zinhos", "zinhas", "zinho", "zinha", "inhos", "inhas", "inho", "inha", "oes", "ona", "onas", "ao", "aco", "aca", "ice",
            "ices", "es", "s")


def stem(word: str) -> str:
    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            word = word[: -len(suf)]
            break
    return word.rstrip("aeo") if len(word) > 4 else word


def phonetic(word: str) -> str:
    """Chave fonética simples do português: ss/c/ç/z -> s, qu/c(a,o,u) -> k, ch/x -> x, lh/nh, h mudo, duplas."""
    w = squash(word, keep=0)
    for a, b in (("ph", "f"), ("ch", "x"), ("sh", "x"), ("lh", "li"), ("nh", "ni"), ("y", "i"), ("w", "v"), ("ck", "k")):
        w = w.replace(a, b)
    w = re.sub(r"qu(?=[ei])", "k", w)
    w = re.sub(r"gu(?=[ei])", "g", w)
    w = re.sub(r"c(?=[ei])", "s", w)
    w = re.sub(r"g(?=[ei])", "j", w)
    w = w.replace("c", "k").replace("q", "k").replace("z", "s")
    w = re.sub(r"(?<=[aeiou])s(?=[aeiou])", "s", w)
    w = w.replace("h", "")
    return re.sub(r"(.)\1+", r"\1", w)


def lev1(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


class Lexicon:
    def __init__(self) -> None:
        self.always: dict[str, str] = {}
        for cat, words in (("palavrao", PROFANITY), ("xingamento", INSULTS), ("odio", SLURS), ("aparencia", APPEARANCE)):
            for w in words:
                self.always[w] = cat
        self.directed: dict[str, str] = {w: cat for cat, ws in DIRECTED.items() for w in ws}
        self.squashed = {squash(w, 0): w for w in self.always}
        # sufixos e fonética só para palavrões/xingamentos com raiz longa; ofensas curtas (bicha, sapatão) só exatas,
        # senão "bicho", "sapato" etc. colidem
        flexible = [w for w in PROFANITY + INSULTS if len(w) >= 5]
        self.stems = {stem(w): w for w in flexible if len(stem(w)) >= 4}
        self.phon = {phonetic(w): w for w in flexible if len(w) >= 6}
        for wrong, right in MISSPELLINGS.items():
            self.always.setdefault(right, "odio" if right in SLURS else "palavrao" if right in PROFANITY else "xingamento")
        self.misspellings = MISSPELLINGS
        self.directed_squashed = {squash(w, 0): w for w in self.directed}

    def lookup(self, token: str) -> str | None:
        """Palavra do léxico 'sempre fora' que este token representa (exato, repetição, sufixo, fonética)."""
        if not token or token in ALLOWLIST or squash(token) in ALLOWLIST:
            return None
        for form in (token, squash(token)):
            if form in self.always:
                return form
        for form in (token, squash(token), squash(token, 0)):
            if form in self.misspellings:
                return self.misspellings[form]
        sq = squash(token, 0)
        if sq in self.squashed and sq not in ALLOWLIST:
            return self.squashed[sq]
        # flexões: só quando um sufixo real sai (plural, aumentativo, diminutivo, -ice), não só a vogal final
        base = squash(token)
        for suf in SUFFIXES:
            if base.endswith(suf) and len(base) - len(suf) >= 4:
                st = stem(base)
                if st in self.stems:
                    return self.stems[st]
                break
        # grafia errada com o mesmo som ("imbessil", "estupidu"); sem distância de edição: erra demais
        if len(token) >= 6:
            ph = phonetic(token)
            if ph in self.phon:
                return self.phon[ph]
        return None

    def directed_form(self, token: str) -> str | None:
        # a lista de permitidas protege palavras comuns do léxico FIXO; ditas sobre a pessoa ("é uma anta") continuam fora
        if token in ALLOWLIST and token not in self.directed:
            return None
        if token in self.directed:
            return token
        sq = squash(token, 0)
        if sq in self.directed_squashed:
            return self.directed_squashed[sq]
        # flexões só com sufixo real ("burrinho", "gordinhas"); sem isso "baixo"~"baixinho", "palha"~"palhaço"…
        base = squash(token)
        if any(base.endswith(suf) and len(base) - len(suf) >= 4 for suf in SUFFIXES):
            st = stem(base)
            for w in self.directed:
                if len(st) >= 4 and stem(w) == st and base.startswith(st):
                    return w
        return None


LEXICON = Lexicon()


SPACED = re.compile(r"(?<![a-z0-9])(?:[a-z0-9@$][\s.\-_,/\\]{1,3}){2,}[a-z0-9@$](?![a-z0-9])")


def tokenize(text: str) -> tuple[list[str], list[str]]:
    """Tokens normalizados + tokens extras vindos de letras separadas e máscaras ("b u r r o", "m.e.r.d.a", "p*ta")."""
    base = fold(text)
    extra: list[str] = []
    # letras isoladas separadas por espaço/ponto/hífen/etc.: "b u r r o" -> "burro" (juntadas no próprio texto, para as
    # regras de contexto também valerem: "professor b u r r o")
    def join(m: re.Match) -> str:
        word = re.sub(r"[^a-z0-9@$]", "", m.group(0)).translate(LEET)
        extra.append(word)
        return f" {word} " if len(word) >= 4 else m.group(0)
    base = SPACED.sub(join, base)
    raw = re.findall(r"[a-z0-9@$!|*#_%]+", base)
    tokens = []
    for t in raw:
        if any(ch in MASK for ch in t) and re.search(r"[a-z]", t):
            extra.append(t)  # tratado como máscara
            continue
        if re.search(r"[a-z]", t) and re.search(r"[0-9@$!|]", t):
            t = t.translate(LEET)
        t = re.sub(r"[^a-z0-9]", "", t)
        if t:
            tokens.append(t)
    return tokens, extra


def mask_match(token: str) -> str | None:
    pattern = re.compile("^" + "".join("[a-z]" if ch in MASK else re.escape(ch) for ch in token.translate(LEET)) + "$")
    for w in list(LEXICON.always):
        if len(w) == len(token) and pattern.match(w):
            return w
    return None


# --------------------------------------------------------------------------- regras de contexto
def _alt(words: list[str]) -> str:
    return "(?:" + "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True)) + ")"


def directed_hits(norm: str, tokens: list[str], professor: str | None) -> list[tuple[str, str]]:
    refs = list(PERSON_REFS)
    if professor:
        names = [t for t in re.findall(r"[a-z]+", fold(professor)) if len(t) >= 3 and t not in ("dos", "das", "de", "da", "do")]
        refs += names
    ref, cop, det = _alt(refs), _alt(COPULAS), _alt(DETS)
    hits = []
    for tok in set(tokens):
        word = LEXICON.directed_form(tok)
        if not word:
            continue
        cat = LEXICON.directed[word]
        t = re.escape(tok)
        patterns = [
            rf"\b{ref}\b(?: \w+)? {cop}(?: {det}){{0,3}}(?: \w+)? {t}\b",   # "ele é um grande burro", "o Renato também é muito velho"
            rf"\b{ref}(?: {det}){{0,2}} {t}\b(?! de\b)",                           # "professor burro", "prof muito velho"
        ]
        if word in DEMONSTRATIVE_OK:
            patterns.append(rf"\b(?:esse|essa|aquele|aquela|seu|sua|o|a) {t}\b(?! (?:de|do|da|que|e)\b)(?=\s+(?:{cop}|nao|n|so|sempre|nunca|deveria|devia|fica|vive|da|deu|passa|passou|tem|nem|$)|$)")
        if any(re.search(p, norm) for p in patterns):
            hits.append((cat, tok))
    # várias palavras de aparência juntas ("gordo, feio e fede") dispensam citar a pessoa
    looks = [i for i, tok in enumerate(tokens) if LEXICON.directed.get(LEXICON.directed_form(tok) or "") == "aparencia"]
    for a, b in zip(looks, looks[1:]):
        if b - a <= 4 and LEXICON.directed_form(tokens[a]) != LEXICON.directed_form(tokens[b]):
            hits.append(("aparencia", f"{tokens[a]} … {tokens[b]}"))
            break
    return hits


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\(\d{2}\)\s?|\b\d{2}[\s.-])9?\d{4}[\s.-]?\d{4}\b|\b9\d{4}[\s.-]?\d{4}\b")
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
MATRICULA = re.compile(r"\bmatr[ií]cula\s*(?:n[ºo°]?\.?)?\s*:?\s*\d{6,}", re.I)
PLATE = re.compile(r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b")
HANDLE = re.compile(r"(?<![\w.])@[a-z0-9_.]{3,}", re.I)
SOCIAL = re.compile(r"\b(?:instagram|insta|twitter|tiktok|facebook|face|whatsapp|whats|zap|telegram|linkedin)\b[^\n]{0,25}?(?:@|/|\d{4})", re.I)
ADDRESS = re.compile(r"\b(?:mora|moram|morava|reside|residia|casa del[ea]|endereco del[ea]|apartamento del[ea]|ap del[ea])\b")
URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|io|ly|gg|me|xyz)(?:\.br)?(?:/\S*)?\b", re.I)


def analyze(comment: str, professor: str | None = None) -> list[Issue]:
    """Lista de problemas encontrados (vazia = publica). Uma ocorrência por categoria."""
    issues: dict[str, Issue] = {}

    def add(cat: str, evidence: str) -> None:
        issues.setdefault(cat, Issue(cat, evidence))

    tokens, extra = tokenize(comment)
    norm = " ".join(tokens)
    # léxico "sempre fora"
    for tok in tokens + [e for e in extra if not any(ch in MASK for ch in e)]:
        word = LEXICON.lookup(tok)
        if word:
            add(LEXICON.always[word], tok)
    for e in extra:
        if any(ch in MASK for ch in e):
            word = mask_match(e)
            if word:
                add(LEXICON.always[word], e)
    # contexto: ditas sobre a pessoa
    for cat, tok in directed_hits(norm, tokens, professor):
        add(cat, tok)
    # expressões
    for cat, patterns in PHRASES.items():
        for p in patterns:
            m = re.search(p, norm)
            if m:
                add(cat, m.group(0))
                break
    # dados pessoais e links
    for m in EMAIL.finditer(comment):
        if not INSTITUTIONAL_EMAIL.search(m.group(0)):
            add("dado_pessoal", m.group(0))
    for rx in (PHONE, CPF, MATRICULA, PLATE, SOCIAL):
        m = rx.search(comment)
        if m:
            add("dado_pessoal", m.group(0))
    without_emails = EMAIL.sub(" ", comment)
    m = HANDLE.search(without_emails)
    if m:
        add("dado_pessoal", m.group(0))
    m = ADDRESS.search(norm)
    if m and re.search(r"\b(?:rua|avenida|av|alameda|travessa|praca|bairro|predio|condominio|perto|numero|n)\b|\d", norm[m.start():m.start() + 80]):
        add("dado_pessoal", m.group(0))
    urls = URL.findall(comment)
    if SHORTENERS.search(comment) or len(urls) >= 3:
        add("link", (urls or [""])[0])
    # forma
    letters = [c for c in comment if c.isalpha()]
    if len(letters) > 30 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        add("maiusculas", comment[:30])
    words = re.findall(r"[a-zà-ú]+", comment.lower())
    if words:
        most = max(words.count(w) for w in set(words))
        no_vowel = sum(1 for w in words if len(w) >= 5 and not re.search(r"[aeiouyà-ú]", w))
        laugh = sum(len(w) for w in words if re.fullmatch(r"(?:k|ha|he|rs|ks)+", w))
        if (len(set(words)) < 5 or (len(words) >= 6 and most / len(words) > 0.4) or no_vowel / len(words) > 0.3
                or laugh / max(1, sum(len(w) for w in words)) > 0.5):
            add("sem_conteudo", " ".join(words[:5]))
    return [Issue(i.category, _original(comment, i.evidence)) for i in issues.values()]


def _original(comment: str, evidence: str) -> str:
    """Mostra o trecho como a pessoa escreveu ("palhaço"), não a forma normalizada ("palhaco")."""
    target = re.sub(r"[^a-z0-9]", "", evidence)
    if not target:
        return evidence
    words = list(re.finditer(r"[\wÀ-ÿ@$*#%!|.-]+", comment))
    for size in (1, 2, 3, 4, 5):
        for i in range(len(words) - size + 1):
            chunk = comment[words[i].start():words[i + size - 1].end()]
            if re.sub(r"[^a-z0-9]", "", fold(chunk).translate(LEET)) == target:
                return chunk
    return evidence


def decide(issues: list[Issue]) -> str:
    if any(i.action == REJECT for i in issues):
        return REJECT
    if any(i.action == HOLD for i in issues):
        return HOLD
    return PUBLISH
