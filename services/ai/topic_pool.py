"""Topic pool + diversity seed for exam-gen v3 spec mode (design §7).

Ported from the client's validated `lib/exam-gen/topicPool.ts` (harness
evidence: two independent runs on the same topic at temperature 1.0 converged
on the SAME story — "Gran's lamb stew"; instruction-level "be diverse" did
nothing; only backend-injected random seeds worked, and the 5th dimension
`specific_subject_hint` with a per-topic mini-pool was needed to stop subject
convergence).

Code constants this round; becomes admin-config in the editable-blocks phase.
KET→A2, PET→B1 (other levels are not spec-eligible — design §3.2).
"""

import random as _random
from typing import Any, Optional

# Each entry: topic + genre + mini-pool of concrete subjects within the topic.
TOPIC_POOL: dict[str, list[dict[str, Any]]] = {
    "A2": [
        {"topic": "a new pet in the family", "genre": "narrative",
         "subjects": ["a rescued grey rabbit", "a noisy green parrot", "a shy old cat from a neighbour", "a goldfish won at a fair"]},
        {"topic": "helping at home at the weekend", "genre": "narrative",
         "subjects": ["repainting the garden fence", "sorting out the garage", "washing the family car", "cooking Sunday lunch together"]},
        {"topic": "a school sports day", "genre": "narrative",
         "subjects": ["the sack race", "a relay race that ends in a tie", "the long jump", "a tug-of-war between classes"]},
        {"topic": "visiting a grandparent", "genre": "narrative",
         "subjects": ["learning an old card game", "a box of old photographs", "helping in the allotment", "baking biscuits from a handwritten recipe"]},
        {"topic": "a birthday surprise for a friend", "genre": "narrative",
         "subjects": ["a homemade photo album", "a treasure hunt around the park", "a surprise picnic", "a cake decorated like a football pitch"]},
        {"topic": "learning to swim", "genre": "narrative",
         "subjects": ["fear of the deep end", "a kind instructor with a silly whistle", "the first full length", "goggles that kept leaking"]},
        {"topic": "a trip to the local market", "genre": "narrative",
         "subjects": ["a stall selling strange fruit", "carrying a heavy basket", "getting lost between the stalls", "an old man selling wooden toys"]},
        {"topic": "the new playground in town", "genre": "article",
         "subjects": ["a climbing wall", "a zip line", "a quiet corner with benches", "a water play area"]},
        {"topic": "a favourite after-school club", "genre": "article",
         "subjects": ["a chess club", "a cooking club", "a robot-building club", "a drawing club"]},
        {"topic": "making breakfast for the family", "genre": "narrative",
         "subjects": ["pancakes that stuck to the pan", "eggs cooked three ways", "a surprise tray for mum", "burnt toast and a happy ending"]},
        {"topic": "a lost school bag", "genre": "narrative",
         "subjects": ["left on the bus", "found in the gym", "taken by mistake by a classmate", "handed in at the library"]},
        {"topic": "a class picnic in the park", "genre": "narrative",
         "subjects": ["ants in the sandwiches", "a frisbee game", "sudden rain and one umbrella", "feeding leftover bread to ducks"]},
        {"topic": "a new student in class", "genre": "narrative",
         "subjects": ["someone who speaks three languages", "a student who loves insects", "a quiet girl who turns out to be funny", "a boy from a village school"]},
        {"topic": "looking after a neighbour's garden", "genre": "narrative",
         "subjects": ["watering tomatoes in a heatwave", "a broken garden gnome", "a cat that kept digging", "sunflowers taller than the fence"]},
        {"topic": "a rainy day at home", "genre": "blog post",
         "subjects": ["building a blanket fort", "a board game marathon", "baking with whatever is in the cupboard", "sorting old toys to give away"]},
        {"topic": "the school library", "genre": "article",
         "subjects": ["a reading corner with beanbags", "a book-swap shelf", "a strict but kind librarian", "a comic-book section"]},
        {"topic": "a bicycle ride with friends", "genre": "narrative",
         "subjects": ["a flat tyre far from home", "a race to the old bridge", "an ice-cream stop", "a shortcut that wasn't shorter"]},
        {"topic": "a visit to the dentist", "genre": "narrative",
         "subjects": ["a waiting room fish tank", "a dentist who tells jokes", "a wobbly tooth", "a sticker for being brave"]},
        {"topic": "collecting stickers", "genre": "blog post",
         "subjects": ["football stickers", "animal stickers", "swapping doubles at break time", "one rare shiny sticker"]},
        {"topic": "a family board game evening", "genre": "narrative",
         "subjects": ["a property trading game", "a memory card game", "a game grandpa always wins", "a new game with confusing rules"]},
        {"topic": "first day at a holiday camp", "genre": "email",
         "subjects": ["a top bunk bed", "a camp song everyone knows", "a shy tent-mate", "a swimming test"]},
        {"topic": "a small vegetable garden at school", "genre": "article",
         "subjects": ["growing radishes", "a scarecrow made in art class", "watering duty", "the first tiny carrot"]},
        {"topic": "a phone call with a cousin", "genre": "narrative",
         "subjects": ["planning a summer visit", "news about a new baby", "help with homework", "a shared joke from last year"]},
        {"topic": "feeding ducks at the pond", "genre": "narrative",
         "subjects": ["a greedy goose", "bringing peas instead of bread", "a duckling family", "an early morning visit"]},
        {"topic": "a homemade kite", "genre": "narrative",
         "subjects": ["newspaper and bamboo sticks", "a tail made of old ribbons", "the first crash", "finally flying on a windy hill"]},
    ],
    "B1": [
        {"topic": "learning to cook a family dish", "genre": "blog post",
         "subjects": ["a spicy noodle soup", "a folded flatbread with herbs", "a festival rice dish", "a fish baked in banana leaves"]},
        {"topic": "joining a new sports club", "genre": "narrative",
         "subjects": ["a badminton club", "a rowing club on the river", "a table-tennis league", "a climbing gym"]},
        {"topic": "a weekend job at an animal shelter", "genre": "narrative",
         "subjects": ["an old three-legged dog", "a room full of rescued cats", "cleaning the bird aviary", "a nervous greyhound learning to trust"]},
        {"topic": "preparing for a school music concert", "genre": "narrative",
         "subjects": ["a clarinet solo", "a choir piece in two languages", "a broken guitar string before the show", "drumming in the finale"]},
        {"topic": "growing vegetables on a balcony", "genre": "blog post",
         "subjects": ["cherry tomatoes in buckets", "chillies on the windowsill", "herbs in hanging pots", "a pumpkin that outgrew its pot"]},
        {"topic": "a city library and its activities", "genre": "article",
         "subjects": ["a coding workshop", "a poetry evening", "a homework help desk", "a local history exhibition"]},
        {"topic": "collecting old coins as a hobby", "genre": "article",
         "subjects": ["a coin found in a grandfather's drawer", "a market stall find", "a coin from a country that no longer exists", "cleaning coins the wrong way"]},
        {"topic": "a cycling trip that went wrong", "genre": "narrative",
         "subjects": ["a wrong turn onto a farm track", "a storm on the coast road", "two flat tyres and one pump", "a closed bridge and a long detour"]},
        {"topic": "helping organise a street market", "genre": "narrative",
         "subjects": ["setting up stalls at dawn", "a cake stall for charity", "a lost cash box", "live music that saved the day"]},
        {"topic": "learning sign language", "genre": "article",
         "subjects": ["signing with a deaf teammate", "an online course with a strict teacher", "learning song lyrics in sign", "a misunderstanding that taught a lesson"]},
        {"topic": "a part-time job in a café", "genre": "narrative",
         "subjects": ["the espresso machine nobody can fix", "a regular customer with a strange order", "the Saturday morning rush", "learning latte art"]},
        {"topic": "moving to a new school", "genre": "narrative",
         "subjects": ["a buddy system that actually worked", "getting lost before the first lesson", "a completely different uniform", "joining the debate team to make friends"]},
        {"topic": "repairing an old bicycle", "genre": "narrative",
         "subjects": ["a rusty chain", "hand-painting the frame", "finding parts at a flea market", "a bell that finally rings"]},
        {"topic": "a community clean-up day", "genre": "article",
         "subjects": ["clearing the riverbank", "a mountain of plastic bottles", "an unexpected find in the bushes", "neighbours who never spoke before"]},
        {"topic": "starting a small podcast with friends", "genre": "blog post",
         "subjects": ["interviewing the school caretaker", "editing out the laughing fits", "a microphone bought second-hand", "the first listener from another country"]},
        {"topic": "a photography competition at school", "genre": "narrative",
         "subjects": ["a photo of morning fog", "breaking a lens the day before", "photographing the school cat", "losing to a younger student"]},
        {"topic": "volunteering at a food bank", "genre": "narrative",
         "subjects": ["sorting tins by date", "a delivery van that broke down", "a thank-you note from a family", "the busiest week of winter"]},
        {"topic": "learning a musical instrument as a teenager", "genre": "blog post",
         "subjects": ["a second-hand violin", "practising in the garage", "sore fingertips from guitar strings", "a first small audience"]},
        {"topic": "a science fair project that surprised everyone", "genre": "narrative",
         "subjects": ["a volcano model that worked too well", "growing crystals", "a robot that drew portraits", "measuring noise in the canteen"]},
        {"topic": "running a school recycling campaign", "genre": "article",
         "subjects": ["bins painted by the art club", "a poster competition", "weighing a month of paper", "convincing the headteacher"]},
        {"topic": "a drama club performance", "genre": "narrative",
         "subjects": ["forgetting lines on opening night", "a costume made of curtains", "playing a villain for the first time", "a power cut during the final scene"]},
        {"topic": "teaching a grandparent to use a smartphone", "genre": "narrative",
         "subjects": ["video calls with relatives abroad", "accidental selfies", "a gardening app", "one very large font size"]},
        {"topic": "a neighbourhood swimming pool in summer", "genre": "article",
         "subjects": ["the early morning lap swimmers", "a lifeguard training day", "an inflatable obstacle course", "the queue on the hottest day"]},
        {"topic": "keeping a weather diary for a school project", "genre": "blog post",
         "subjects": ["a homemade rain gauge", "a week of record heat", "photographing clouds", "grandad's weather sayings put to the test"]},
        {"topic": "organising a surprise farewell for a teacher", "genre": "narrative",
         "subjects": ["a memory book signed in secret", "a song rehearsed at lunchtimes", "keeping the secret for two weeks", "a cake shaped like a textbook"]},
    ],
}

LEVEL_TO_CEFR = {"KET": "A2", "PET": "B1"}  # other levels: not spec-eligible

_NARRATORS = [
    "a teenage girl", "a teenage boy", "a 12-year-old who is new to the activity",
    "a quiet teenager who prefers watching to joining in",
    "a confident teenager who discovers they were wrong about something",
    "a teenager who is doing this activity for the first time",
]
_OTHER_PERSON_ROLES = [
    "a grandfather", "an aunt", "a neighbour", "a coach", "an older sister",
    "a younger brother", "a family friend", "a strict but kind teacher",
    "a classmate they barely knew before", "an uncle who lives far away",
]
_SETTING_DETAILS = [
    "a small flat kitchen", "a community hall", "a rainy Saturday",
    "a hot summer afternoon", "a crowded local market", "an early winter morning",
    "a quiet street at the edge of town", "a busy school corridor",
    "an old building with creaky stairs", "a garden shared by several families",
]
_COMPLICATIONS = [
    "the first attempt fails", "an unexpected guest arrives",
    "a key ingredient or tool is missing", "there is unexpected time pressure",
    "it is being done for an important event", "the weather changes suddenly",
    "an embarrassing mistake happens in front of others",
    "someone who was supposed to help cannot come",
]
# Known-weak fallback for admin-typed topics (harness: generic hint still
# converged on "stew-like" methods) — per-topic mini-pools are the real fix.
GENERIC_SUBJECT_HINTS = [
    "build the story around a specific central object, event or activity that "
    "is NOT the most typical choice for this topic",
    "invent one unusual concrete detail and make it the centre of the story",
    "pick a specific focus within the topic that a typical writer would not "
    "choose first",
]


def pick_topic_and_seed(
    level: str,
    rng: Optional[_random.Random] = None,
    *,
    admin_topic: Optional[str] = None,
) -> dict[str, Any]:
    """Pick {topic, genre, diversity_seed} for one GENERATE attempt.

    Admin topic (from sectionPrompts) beats the random pool; its subject hint
    falls back to the generic pool. Call again on every regenerate retry —
    the seed must be RE-ROLLED per attempt (design §4/M4); the caller logs the
    seed of the SUCCESSFUL round only.
    """
    rng = rng or _random
    cefr = LEVEL_TO_CEFR.get(level)
    if admin_topic and admin_topic.strip():
        topic, genre = admin_topic.strip(), "appropriate to the topic"
        subjects: list[str] = []
    else:
        entry = rng.choice(TOPIC_POOL[cefr or "A2"])
        topic, genre, subjects = entry["topic"], entry["genre"], entry["subjects"]
    return {
        "topic": topic,
        "genre": genre,
        "diversity_seed": {
            "narrator": rng.choice(_NARRATORS),
            "other_person_role": rng.choice(_OTHER_PERSON_ROLES),
            "setting_detail": rng.choice(_SETTING_DETAILS),
            "complication": rng.choice(_COMPLICATIONS),
            "specific_subject_hint": rng.choice(subjects or GENERIC_SUBJECT_HINTS),
        },
    }
