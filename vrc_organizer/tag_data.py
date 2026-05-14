"""Avatar, genre, and common-term dictionaries for auto-tagging and autocomplete.

Word list maps lowercase tokens to canonical tag names.
Tag hierarchy defines parent-child implication rules.
"""

from __future__ import annotations

# ── Top Avatars (by community popularity, from marketplace stats) ──
# Ordered by number of compatible items listed on the marketplace.
# 240 avatars in descending popularity order.
TOP_AVATARS: list[str] = [
    "Kipfel", "Shinano", "Manuka", "Milltina", "rurune", "mamehinata", "Chocolat",
    "Shinra", "Selestia", "Kikyo", "Minase", "Sio", "Milfy", "Rinasciita", "Komano",
    "Mafuyu", "Eku", "Chiffon", "Karin", "Lumina", "Marycia", "mao", "Moe",
    "Lasyusha", "Ichigo", "Rusk", "Maya", "Mizuki", "Hakka", "Airi", "Zome",
    "Lapwing", "Deltaflair", "Lime", "Kanata", "Rindo", "Bokusei", "Hanka", "Nagi",
    "Alué", "Ghost Fox Sister", "RadDollV2", "usasaki", "Ash", "Kuuta", "nemesis",
    "Nanase Noir", "ELusion", "Felis", "Yoll", "Miminoko", "Platinum", "Sapphy",
    "Nayu", "Sophina", "Wolferia", "New NecoMaid", "U (QuQu)", "Anri", "Shiratsume",
    "ririka", "Mishe", "Grus", "Fiona", "Kokoa", "Liloumois", "minahoshi", "Mint",
    "Lazuli", "Koyuki", "Shizuku-san", "Soraha", "KALNE", "HAOLAN", "Maki", "Shuan",
    "Flare", "Low-Poly Kon (Archive 2019)", "Leefa", "Iris", "Kaya", "Lunalitt",
    "Scented", "patoto", "Imeris", "Cian", "Milk", "inuinu", "Leeme & Reeva",
    "Chalo", "meiyun", "Lzebul", "Anon", "Kyoko", "Binah", "Merino", "CyberCat2.0",
    "Velle", "Yugi & Miyo", "Tolass & Wedge", "Azuki", "Nagiya Ruri", "Kyubi",
    "Anomea", "INABA", "Nemo", "Eyo", "Kuronatsu", "RearAlice", "Mururu", "Mulicia",
    "Hatsune Miku", "Cornet", "Hamster", "Foshunia", "Kirise", "Tonnerre", "Aldina",
    "Adolescent U", "Mariel", "LilLeo", "Rize", "Sue", "Ciel (Regular)", "Munkki",
    "Menno", "mashu", "haishima", "Puma the Puma", "Snow Elf Lady", "Makina",
    "Satalina Maid", "Kosame", "Nanodevi", "Emmelie", "Sephira", "Toah", "Darjelling",
    "LETHALFREET", "Gimozard", "Lucifer", "UltimateKissMa", "Mochikichi & Komochi",
    "Noy", "Isanai Nuku", "Sipilka", "Peke (QuQu)", "Beryl", "Chimaco Tribe", "Alu",
    "Nozomi", "Milk & Sugar", "Senneko", "Marshmellow", "Clara", "Retinia", "Kohaku",
    "Sii & Phi", "Shayna", "Intānetto no Pomae", "Pomemaru & Kopomemaru", "YUGIRI",
    "GELAS", "uomaru", "Ururu", "Moko", "nyago", "Sairou", "Wolfram", "Pon & Chune",
    "MUMUS", "Celeate", "Shiratori", "Medical Center Staff", "Fufu", "SvartLilja",
    "Marumaru", "Rei", "Julius", "Sanatia", "Lyphos", "Uruki", "Helvetica",
    "Lena Cocktail", "Saneko", "Nekoyama Nae", "Honoka", "Qunyan", "RUGINEA",
    "Kyalóng", "Kalkia", "Quish", "Noiz", "Inaba Kaya", "patanyako", "Amagi", "Ilio",
    "Seiran", "Runa", "Rufina", "Nix", "Nenmir", "Mophira", "Mia", "Lucife", "SHRI",
    "Uzuruha", "Kisha", "suzuhana", "Rabbit Hole Miku", "Perula", "Tycho", "Fluctua",
    "Perfect Maid Mellotron", "TubeRose", "ninya", "Hina", "Xena", "TUNER", "Maneko",
    "Yoru", "Shinonome", "Shimoe Koharu", "Danzai", "Rainy", "Rushina", "Eve",
    "Machi", "Sirius", "SOKA", "Ryuon", "Yuki", "Yuuko", "Snow White Angel", "Runya",
    "pochimaru", "Tsubaki", "Stier", "Luruleah", "Ukon Second Type", "Cyanos",
]

# Japanese avatar name → canonical English tag name
JP_AVATAR_TO_EN: dict[str, str] = {
    "キプフェル": "Kipfel", "きぷふぇる": "Kipfel",
    "ルルネ": "Rurune", "るるね": "Rurune",
    "まめひなた": "Mamehinata", "マメヒナタ": "Mamehinata",
    "まめふれんず": "Mamehinata", "マメフレンズ": "Mamehinata",
    "mamefriends": "Mamehinata", "mame_friends": "Mamehinata",
    "マヌカ": "Manuka", "まぬか": "Manuka",
    "ショコラ": "Chocolat", "しょこら": "Chocolat",
    "ちょこら": "Chocolat", "チョコラ": "Chocolat",
    "みみのこ": "Miminoko", "ミミノコ": "Miminoko",
    "ゆうこ": "Yuuko", "ユウコ": "Yuuko", "yuuko": "Yuuko", "yuko": "Yuuko",
    "まお": "Mao", "マオ": "Mao",
    "シフォン": "Chiffon", "しふぉん": "Chiffon",
    "桔梗": "Kikyo", "キキョウ": "Kikyo", "ききょう": "Kikyo",
    "ラスク": "Rusk", "らすく": "Rusk",
    "セレスティア": "Selestia", "せれすてぃあ": "Selestia",
    "ミント": "Mint", "みんと": "Mint",
    "リリウム": "Lillium", "りりうむ": "Lillium",
    "ヤヨイ": "Yayoi", "やよい": "Yayoi",
    "モエ": "Moe", "もえ": "Moe",
    "フォクシー": "Foxy", "ふぉくしー": "Foxy",
    "レックス": "Rex", "れっくす": "Rex",
    "ずんだもん": "Zundamon", "ズンダモン": "Zundamon",
    "シバ": "Shiba", "しば": "Shiba",
    "ライス": "Rice", "らいす": "Rice",
    "シナモン": "Cinnamon", "しなもん": "Cinnamon",
    "メープル": "Maple", "めーぷる": "Maple",
    "マヤ": "Maya", "まや": "Maya",
    "アメリア": "Amelia", "あめりあ": "Amelia",
    "リンド": "Rindo", "りんど": "Rindo",
    "ツムギ": "Tsumugi", "つむぎ": "Tsumugi",
    "コムギ": "Komugi", "こむぎ": "Komugi",
    "ライム": "Lime", "らいむ": "Lime",
    "アオイ": "Aoi", "あおい": "Aoi",
    "コハク": "Kohaku", "こはく": "Kohaku",
    "スズ": "Suzu", "すず": "Suzu",
    "ニャー": "Nyah", "にゃー": "Nyah",
    "トロリ": "Torori", "とろり": "Torori",
    "セシル": "Cecil", "せしる": "Cecil",
    "ミリー": "Milly", "みりー": "Milly",
    "イロハ": "Iroha", "いろは": "Iroha",
    "ナナチ": "Nanachi", "ななち": "Nanachi",
    "クロネコ": "Kuroneko", "くろねこ": "Kuroneko",
    "ルルミ": "Rurumi", "るるみ": "Rurumi",
    "モモ": "Momo", "もも": "Momo",
    "ユイ": "Yui", "ゆい": "Yui",
    "ステラ": "Stella", "すてら": "Stella",
    "ミオ": "Mio", "みお": "Mio",
    "ヒヨリ": "Hiyori", "ひより": "Hiyori",
    "カンナ": "Kanna", "かんな": "Kanna",
    "シンラ": "Shinra", "しんら": "Shinra",
    "アリア": "Aria", "ありあ": "Aria",
    "リリス": "Lilith", "りりす": "Lilith",
    "ナギサ": "Nagisa", "なぎさ": "Nagisa",
}

# ── Genre/category terms ───────────────────────────
GENRE_TERMS: list[str] = [
    "Avatar Base", "Outfit", "Accessory", "Hair", "Texture",
    "Shader", "Animation", "Gesture", "Expression", "Emote",
    "Prop", "Weapon", "Wings", "Tail", "Ears", "Horns",
    "Tattoo", "Makeup", "Chibi", "Kemono", "Furry",
    "Neko", "Inumimi", "Fox", "Elf", "Maid",
    "School Uniform", "Military Uniform", "Cyberpunk", "Fantasy",
    "Gothic", "Lolita", "Kimono", "Yukata", "Swimsuit",
    "Lingerie", "Pajamas", "Sportswear", "Gimmick", "Prefab", "Pose",
]

# ── Tag Hierarchy ──────────────────────────────────
# parent → set of child tags. When a child is detected, the parent is auto-added.
TAG_HIERARCHY: dict[str, set[str]] = {
    "Outfit": {
        "Dress", "Skirt", "Pants", "Shorts", "Shirt", "Jacket",
        "Sweater", "Hoodie", "Vest", "Coat", "Tops",
        "Bodysuit", "Corset", "Jumpsuit",
        "Maid", "School Uniform", "Military Uniform", "Kimono",
        "Yukata", "Swimsuit", "Lingerie", "Pajamas", "Sportswear",
        "Suit", "Gothic", "Lolita", "Cyberpunk", "Fantasy", "Idol Outfit",
        "Wedding", "Bunny Suit",
        "Shoes", "Heels", "Boots", "Sandals", "Socks", "Stockings", "Gloves",
    },
    "Accessory": {
        "Hat", "Glasses", "Mask", "Necklace", "Choker", "Earrings",
        "Bracelet", "Ring", "Bag", "Hair Accessory",
        "Ribbon", "Collar", "Wings", "Tail", "Ears", "Horns",
        "Weapon", "Shield", "Prop", "Cape", "Scarf", "Belt",
        "Eyes", "Eyebrows", "Eyelashes", "Fangs",
        "Paws", "Claws",
        "Piercings", "Chains", "Muscle",
    },
    "Hair": {
        "Hairstyle", "Bangs", "Ponytail", "Twin Tails", "Bob Cut",
        "Long Hair", "Short Hair", "Braids", "Ahoge",
    },
    "Material": {
        "Texture", "Shader", "lilToon", "Poiyomi",
    },
    "Expression": {
        "Gesture", "Animation", "Emote", "Pose", "Dance",
    },
}

# ── Word → Tag Mapping ─────────────────────────────
# Lowercase tokens match to canonical tag names.
# Broad categories that cover most VRChat assets with 2-5 meaningful tags.
WORD_TO_TAG: dict[str, str] = {
    # ── Outfit / Clothing (main garments) ──
    "outfit": "Outfit", "clothing": "Outfit", "clothes": "Outfit", "衣装": "Outfit", "コーデ": "Outfit", "服": "Outfit",
    "dress": "Dress", "ドレス": "Dress", "onepiece": "Dress", "ワンピース": "Dress",
    "skirt": "Skirt", "スカート": "Skirt",
    "pants": "Pants", "パンツ": "Pants", "trousers": "Pants", "jeans": "Pants", "ズボン": "Pants",
    "shorts": "Shorts", "ショーツ": "Shorts", "ショートパンツ": "Shorts",
    "shirt": "Shirt", "シャツ": "Shirt", "blouse": "Shirt", "tshirt": "Shirt", "ブラウス": "Shirt",
    "jacket": "Jacket", "ジャケット": "Jacket", "blazer": "Jacket", "cardigan": "Jacket",
    "sweater": "Sweater", "セーター": "Sweater", "ニット": "Sweater",
    "hoodie": "Hoodie", "パーカー": "Hoodie", "parka": "Hoodie",
    "vest": "Vest", "ベスト": "Vest",
    "coat": "Coat", "コート": "Coat",
    "tops": "Tops", "トップス": "Tops", "tanktop": "Tops", "croptop": "Tops", "タンクトップ": "Tops",
    "bodysuit": "Bodysuit", "ボディスーツ": "Bodysuit", "leotard": "Bodysuit", "レオタード": "Bodysuit",
    "corset": "Corset", "コルセット": "Corset",
    "jumpsuit": "Jumpsuit", "overalls": "Jumpsuit", "つなぎ": "Jumpsuit",

    # ── Outfit Styles / Themes ──
    "maid": "Maid", "メイド": "Maid",
    "uniform": "School Uniform", "制服": "School Uniform", "seifuku": "School Uniform",
    "military": "Military Uniform", "ミリタリー": "Military Uniform", "army": "Military Uniform",
    "kimono": "Kimono", "着物": "Kimono",
    "yukata": "Yukata", "浴衣": "Yukata",
    "swimsuit": "Swimsuit", "bikini": "Swimsuit", "水着": "Swimsuit",
    "lingerie": "Lingerie", "ランジェリー": "Lingerie", "下着": "Lingerie",
    "pajamas": "Pajamas", "パジャマ": "Pajamas",
    "sportswear": "Sportswear", "jersey": "Sportswear", "ジャージ": "Sportswear",
    "suit": "Suit", "スーツ": "Suit",
    "gothic": "Gothic", "ゴシック": "Gothic", "goth": "Gothic",
    "lolita": "Lolita", "ロリータ": "Lolita",
    "cyberpunk": "Cyberpunk", "サイバー": "Cyberpunk", "cyber": "Cyberpunk",
    "fantasy": "Fantasy", "ファンタジー": "Fantasy",
    "idol": "Idol Outfit", "アイドル": "Idol Outfit",
    "casual": "Casual", "カジュアル": "Casual",
    "wedding": "Wedding", "ウェディング": "Wedding", "ウエディング": "Wedding",
    "halloween": "Halloween", "ハロウィン": "Halloween",
    "christmas": "Christmas", "クリスマス": "Christmas", "xmas": "Christmas",
    "magical": "Magical Girl", "魔法少女": "Magical Girl", "magicalgirl": "Magical Girl",

    # ── Accessories ──
    "accessory": "Accessory", "アクセサリー": "Accessory", "アクセ": "Accessory",
    "hat": "Hat", "帽子": "Hat", "cap": "Hat", "beanie": "Hat", "beret": "Hat", "ベレー": "Hat",
    "glasses": "Glasses", "眼鏡": "Glasses", "megane": "Glasses", "メガネ": "Glasses",
    "mask": "Mask", "マスク": "Mask",
    "necklace": "Necklace", "ネックレス": "Necklace", "pendant": "Necklace",
    "choker": "Choker", "チョーカー": "Choker",
    "earrings": "Earrings", "earring": "Earrings", "ピアス": "Earrings", "イヤリング": "Earrings",
    "bracelet": "Bracelet", "ブレスレット": "Bracelet",
    "ring": "Ring", "リング": "Ring", "指輪": "Ring",
    "bag": "Bag", "バッグ": "Bag", "backpack": "Bag", "リュック": "Bag",
    "hairpin": "Hair Accessory", "hairclip": "Hair Accessory", "headband": "Hair Accessory",
    "髪飾り": "Hair Accessory", "ヘアピン": "Hair Accessory", "カチューシャ": "Hair Accessory",
    "ribbon": "Ribbon", "リボン": "Ribbon",
    "collar": "Collar", "カラー": "Collar", "首輪": "Collar",

    # ── Body Parts (sold separately) ──
    "wings": "Wings", "wing": "Wings", "翼": "Wings", "羽": "Wings", "羽根": "Wings",
    "tail": "Tail", "尻尾": "Tail", "しっぽ": "Tail", "テール": "Tail",
    "ears": "Ears", "ear": "Ears", "耳": "Ears", "nekomimi": "Ears", "kemonomimi": "Ears", "ケモミミ": "Ears",
    "horns": "Horns", "horn": "Horns", "角": "Horns", "ツノ": "Horns",
    "fangs": "Fangs", "fang": "Fangs", "牙": "Fangs", "八重歯": "Fangs",
    "eyes": "Eyes", "eye": "Eyes", "目": "Eyes", "瞳": "Eyes", "アイ": "Eyes",
    "eyebrows": "Eyebrows", "eyebrow": "Eyebrows", "眉毛": "Eyebrows", "まゆげ": "Eyebrows",
    "eyelashes": "Eyelashes", "eyelash": "Eyelashes", "まつげ": "Eyelashes", "睫毛": "Eyelashes",
    "paws": "Paws", "paw": "Paws", "肉球": "Paws",
    "claws": "Claws", "claw": "Claws", "爪": "Claws",
    "piercings": "Piercings", "piercing": "Piercings",
    "chains": "Chains", "chain": "Chains", "チェーン": "Chains", "鎖": "Chains",
    "muscle": "Muscle", "muscles": "Muscle", "筋肉": "Muscle", "マッスル": "Muscle",

    # ── Weapons / Props ──
    "weapon": "Weapon", "武器": "Weapon", "sword": "Weapon", "gun": "Weapon", "katana": "Weapon",
    "剣": "Weapon", "刀": "Weapon", "銃": "Weapon",
    "shield": "Shield", "盾": "Shield",
    "prop": "Prop", "小物": "Prop",

    # ── Worn Items ──
    "cape": "Cape", "マント": "Cape", "cloak": "Cape",
    "scarf": "Scarf", "マフラー": "Scarf", "スカーフ": "Scarf",
    "belt": "Belt", "ベルト": "Belt",

    # ── Footwear / Legwear ──
    "shoes": "Shoes", "靴": "Shoes", "シューズ": "Shoes", "sneakers": "Shoes", "sneaker": "Shoes", "スニーカー": "Shoes",
    "loafers": "Shoes", "platforms": "Shoes", "platform": "Shoes", "creepers": "Shoes",
    "footwear": "Shoes", "maryjanes": "Shoes",
    "heels": "Heels", "heel": "Heels", "ハイヒール": "Heels", "pumps": "Heels", "パンプス": "Heels",
    "boots": "Boots", "boot": "Boots", "ブーツ": "Boots",
    "sandals": "Sandals", "sandal": "Sandals", "サンダル": "Sandals",
    "socks": "Socks", "靴下": "Socks", "ソックス": "Socks",
    "stockings": "Stockings", "ストッキング": "Stockings", "thighhigh": "Stockings", "ニーハイ": "Stockings",
    "tights": "Stockings", "タイツ": "Stockings",
    "gloves": "Gloves", "手袋": "Gloves", "グローブ": "Gloves",

    # ── Hair ──
    "hair": "Hair", "髪": "Hair", "ヘアー": "Hair",
    "hairstyle": "Hairstyle", "ヘアスタイル": "Hairstyle", "髪型": "Hairstyle",
    "bangs": "Bangs", "前髪": "Bangs",
    "ponytail": "Ponytail", "ポニーテール": "Ponytail", "ポニテ": "Ponytail",
    "twintails": "Twin Tails", "twintail": "Twin Tails", "ツインテール": "Twin Tails", "ツインテ": "Twin Tails",
    "bobcut": "Bob Cut", "ボブ": "Bob Cut", "ボブカット": "Bob Cut",
    "longhair": "Long Hair", "ロング": "Long Hair", "ロングヘア": "Long Hair",
    "shorthair": "Short Hair", "ショート": "Short Hair", "ショートヘア": "Short Hair",
    "braids": "Braids", "braid": "Braids", "三つ編み": "Braids", "編み込み": "Braids",
    "ahoge": "Ahoge", "アホ毛": "Ahoge",

    # ── Body Modifications ──
    "makeup": "Makeup", "メイク": "Makeup", "化粧": "Makeup",
    "tattoo": "Tattoo", "タトゥー": "Tattoo",
    "chibi": "Chibi", "ちび": "Chibi", "SD": "Chibi",

    # ── Texture / Material ──
    "texture": "Texture", "テクスチャ": "Texture",
    "material": "Material", "マテリアル": "Material",
    "shader": "Shader", "シェーダー": "Shader",
    "recolor": "Recolor", "色替え": "Recolor", "色変え": "Recolor",

    # ── Animation ──
    "animation": "Animation", "アニメーション": "Animation", "anim": "Animation",
    "gesture": "Gesture", "ジェスチャー": "Gesture", "ハンドサイン": "Gesture",
    "expression": "Expression", "表情": "Expression",
    "emote": "Emote", "エモート": "Emote",
    "pose": "Pose", "ポーズ": "Pose",
    "dance": "Dance", "ダンス": "Dance",
    "idle": "Animation", "待機": "Animation",
    "locomotion": "Locomotion", "歩行": "Locomotion", "移動": "Locomotion",

    # ── Species / Character Types — core VRChat themes only ──
    # (kemono/animal-ear themes are core; fantasy/period themes like
    # samurai/ninja/knight/oni/vampire are too rare to auto-tag and were
    # mostly producing wrong genre classifications.)
    "kemono": "Kemono", "ケモノ": "Kemono", "獣人": "Kemono",
    "furry": "Furry",
    "neko": "Neko", "猫": "Neko", "ネコ": "Neko",
    "inu": "Inumimi", "犬": "Inumimi", "inumimi": "Inumimi", "犬耳": "Inumimi",
    "kitsune": "Fox", "fox": "Fox", "狐": "Fox", "キツネ": "Fox",
    "usagi": "Usagi", "うさぎ": "Usagi", "bunny": "Usagi", "ウサギ": "Usagi",
    "elf": "Elf", "エルフ": "Elf",
    "doll": "Doll", "人形": "Doll", "ドール": "Doll",
    "miko": "Miko", "巫女": "Miko",
    "bunnysuit": "Bunny Suit", "バニー": "Bunny Suit",

    # ── VRChat Technical ──
    "avatar": "Avatar Base", "アバター": "Avatar Base", "素体": "Avatar Base",
    "vroid": "VRoid",
    "prefab": "Prefab", "プレハブ": "Prefab",
    "gimmick": "Gimmick", "ギミック": "Gimmick",
    "facetracking": "Face Tracking", "facetrack": "Face Tracking", "ft": "Face Tracking",
    "フェイストラッキング": "Face Tracking", "フェイスト": "Face Tracking",
    "physbone": "PhysBone", "physbones": "PhysBone", "フィズボーン": "PhysBone", "揺れ物": "PhysBone",
    "mmd": "MMD", "ミクミク": "MMD",
    "quest": "Quest", "クエスト": "Quest", "quest対応": "Quest",
    "pconly": "PC", "PC専用": "PC",
    "addon": "Add-on", "追加": "Add-on", "拡張": "Add-on",
    "改変": "Modification",
    "nsfw": "NSFW", "r18": "NSFW", "r-18": "NSFW",
    # Penetration shader systems — VRChat NSFW context
    "dps": "NSFW", "tps": "NSFW", "sps": "NSFW",
    # Modern VRChat tooling — universally referenced in modern asset bundles
    "vrcfury": "VRCFury", "vrcf": "VRCFury",
    "modularavatar": "Modular Avatar", "modular": "Modular Avatar",
    "toggle": "Toggle", "トグル": "Toggle", "toggles": "Toggle",
    "blendshape": "BlendShape", "blendshapes": "BlendShape",
    "ブレンドシェイプ": "BlendShape", "シェイプキー": "BlendShape",
    # Common shader names
    "liltoon": "lilToon",
    "poiyomi": "Poiyomi",
    # Discovery / aesthetic attributes
    "kawaii": "Cute", "かわいい": "Cute", "cute": "Cute", "可愛い": "Cute",
    "mascot": "Mascot", "マスコット": "Mascot",
}

# ── Canonical genre names (single source of truth) ──
GENRE_NAMES: list[str] = ["Avatar Base", "Outfit & Acce", "Gimmick", "Tools"]

# ── All avatar names as a set for O(1) membership ──
ALL_AVATAR_NAMES: set[str] = set(TOP_AVATARS)
