TRAINING_IMAGE = (
    'https://firebasestorage.googleapis.com/v0/b/parkguideapp-c8517.firebasestorage.app/o/'
    'assests%2F360%2FAdobeStock_15550322.jpeg?alt=media&token=c9d64eed-c48a-4075-b9e3-35314566cd68'
)


SCENARIOS = [
    {
        'code': 'vr-biodiversity-canopy-briefing',
        'scenario_type': 'biodiversity',
        'difficulty': 'intermediate',
        'duration_minutes': 14,
        'order': 1,
        'title': {
            'en': 'Biodiversity Briefing in a Simulated Rainforest',
            'ms': 'Taklimat Biodiversiti dalam Hutan Hujan Simulasi',
            'zh': '模拟雨林生物多样性讲解',
        },
        'description': {
            'en': 'Practise a guide talk that connects canopy, understory, forest floor, and species relationships in one immersive scene.',
            'ms': 'Latih penerangan pemandu yang menghubungkan kanopi, lapisan bawah, lantai hutan, dan hubungan spesies.',
            'zh': '练习将林冠、林下层、森林地面和物种关系联系起来的沉浸式导览。',
        },
        'field_brief': {
            'en': 'A group of first-time visitors is entering a sensitive rainforest trail. Build a clear explanation without encouraging off-trail movement.',
            'ms': 'Sekumpulan pelawat baharu memasuki laluan hutan sensitif. Beri penerangan jelas tanpa menggalakkan keluar laluan.',
            'zh': '一组首次到访者进入敏感雨林步道。请清楚讲解，同时避免鼓励离开步道。',
        },
        'learning_objectives': [
            {'en': 'Explain biodiversity through visible evidence in the environment.'},
            {'en': 'Connect species, habitat layers, and nutrient cycling.'},
            {'en': 'Use low-impact visitor instructions while teaching.'},
        ],
        'success_criteria': [
            {'en': 'Scans the full scene before answering.'},
            {'en': 'Discovers every ecological hotspot.'},
            {'en': 'Scores at least 70% on scenario decisions.'},
        ],
        'panoramas': [
            {
                'name': 'Rainforest learning stop',
                'panorama_url': TRAINING_IMAGE,
                'hotspots': [
                    {
                        'hotspot_id': 'canopy-niches',
                        'title': {'en': 'Canopy Niches', 'ms': 'Nis Kanopi', 'zh': '林冠生态位'},
                        'position_yaw': 34,
                        'position_pitch': 26,
                        'icon_type': 'tree',
                        'color_hint': '#2E7D32',
                        'content': {
                            'description': {
                                'en': 'Use the canopy to explain food, shade, nesting spaces, and why multiple species can share one forest layer.',
                            },
                            'visitor_prompt': {
                                'en': 'Visitor asks: "Why are there so many plants growing at different heights?"',
                            },
                            'guide_action': {
                                'en': 'Give a short layered-forest explanation and ask visitors to observe without touching branches.',
                            },
                        },
                    },
                    {
                        'hotspot_id': 'understory-regeneration',
                        'title': {'en': 'Understory Regeneration', 'ms': 'Pemulihan Lapisan Bawah', 'zh': '林下更新'},
                        'position_yaw': 128,
                        'position_pitch': 2,
                        'icon_type': 'sprout',
                        'color_hint': '#43A047',
                        'content': {
                            'description': {
                                'en': 'Point out seedlings and shade-tolerant plants as evidence of forest recovery and succession.',
                            },
                            'visitor_prompt': {'en': 'Visitor steps toward a seedling for a photo.'},
                            'guide_action': {'en': 'Redirect them to the trail edge and explain how trampling affects regeneration.'},
                        },
                    },
                    {
                        'hotspot_id': 'forest-floor-cycle',
                        'title': {'en': 'Forest Floor Nutrient Cycle', 'ms': 'Kitaran Nutrien Lantai Hutan', 'zh': '森林地面养分循环'},
                        'position_yaw': 238,
                        'position_pitch': -24,
                        'icon_type': 'mushroom',
                        'color_hint': '#795548',
                        'content': {
                            'description': {'en': 'Use leaf litter, fallen logs, and fungi to explain nutrient cycling and microhabitats.'},
                            'visitor_prompt': {'en': 'Visitor asks if fallen branches should be removed to make the trail look cleaner.'},
                            'guide_action': {'en': 'Explain ecological value and distinguish natural debris from trail hazards.'},
                        },
                    },
                ],
            }
        ],
        'quizzes': [
            {
                'question_text': {'en': 'A visitor wants to leave the trail to photograph a seedling. What should the guide do?'},
                'options': {
                    'en': [
                        'Politely keep them on the trail and explain regeneration damage.',
                        'Allow it because seedlings grow back quickly.',
                        'Pick up the seedling and show it to the group.',
                        'Tell the group to hurry without explaining why.',
                    ]
                },
                'correct_option_index': 0,
                'correct_explanation': {'en': 'Correct. The guide protects the site while turning the moment into learning.'},
                'incorrect_explanation': {'en': 'The best response combines visitor management with biodiversity education.'},
            }
        ],
    },
    {
        'code': 'ar-ecotourism-low-impact-trail',
        'scenario_type': 'ecotourism',
        'difficulty': 'beginner',
        'duration_minutes': 12,
        'order': 2,
        'title': {'en': 'Eco-tourism Trail Management Simulation', 'ms': 'Simulasi Pengurusan Laluan Eko-pelancongan', 'zh': '生态旅游步道管理模拟'},
        'description': {'en': 'Practise group spacing, photo-stop control, waste prevention, and leave-no-trace messages during a busy trail stop.'},
        'field_brief': {'en': 'Your group reaches a narrow viewpoint. Several visitors want photos, snacks, and shortcuts.'},
        'learning_objectives': [
            {'en': 'Keep visitor movement safe and low impact.'},
            {'en': 'Explain eco-tourism practices in friendly language.'},
            {'en': 'Balance visitor enjoyment with site protection.'},
        ],
        'success_criteria': [
            {'en': 'Identifies risky visitor behaviours.'},
            {'en': 'Chooses low-impact guide interventions.'},
            {'en': 'Completes the scenario decision check.'},
        ],
        'panoramas': [
            {
                'name': 'Busy eco-trail viewpoint',
                'panorama_url': TRAINING_IMAGE,
                'hotspots': [
                    {
                        'hotspot_id': 'photo-stop',
                        'title': {'en': 'Photo Stop Control'},
                        'position_yaw': 42,
                        'position_pitch': 8,
                        'icon_type': 'camera-marker',
                        'color_hint': '#00897B',
                        'content': {
                            'description': {'en': 'Set a safe photo boundary and rotate small groups through the viewpoint.'},
                            'visitor_prompt': {'en': 'Two visitors step beyond the barrier for a better angle.'},
                            'guide_action': {'en': 'Call them back calmly, offer a safe photo spot, and explain erosion risk.'},
                        },
                    },
                    {
                        'hotspot_id': 'snack-waste',
                        'title': {'en': 'Snack Waste Risk'},
                        'position_yaw': 164,
                        'position_pitch': -12,
                        'icon_type': 'trash-can-outline',
                        'color_hint': '#00695C',
                        'content': {
                            'description': {'en': 'Connect food waste to wildlife habituation, trail cleanliness, and visitor responsibility.'},
                            'visitor_prompt': {'en': 'A wrapper falls near the trail edge.'},
                            'guide_action': {'en': 'Pause the group, recover the wrapper safely, and reinforce pack-in pack-out practice.'},
                        },
                    },
                    {
                        'hotspot_id': 'group-spacing',
                        'title': {'en': 'Group Spacing'},
                        'position_yaw': 292,
                        'position_pitch': 4,
                        'icon_type': 'account-group',
                        'color_hint': '#00A896',
                        'content': {
                            'description': {'en': 'Use spacing and short stops to avoid blocking other trail users and stressing narrow sections.'},
                            'visitor_prompt': {'en': 'The group bunches together on a narrow boardwalk.'},
                            'guide_action': {'en': 'Move the group in pairs and keep explanations short until there is a wider stop.'},
                        },
                    },
                ],
            }
        ],
        'quizzes': [
            {
                'question_text': {'en': 'Visitors want to step over a barrier for a photo. What is the best guide response?'},
                'options': {'en': ['Redirect them to a safe angle and explain erosion risk.', 'Let them go one at a time.', 'Take the photo quickly for them.', 'Cancel the whole stop immediately.']},
                'correct_option_index': 0,
                'correct_explanation': {'en': 'Correct. Good eco-tourism guidance protects the site while preserving visitor experience.'},
                'incorrect_explanation': {'en': 'The strongest answer combines safety, site protection, and visitor-friendly communication.'},
            }
        ],
    },
    {
        'code': 'vr-wildlife-encounter-response',
        'scenario_type': 'wildlife',
        'difficulty': 'advanced',
        'duration_minutes': 15,
        'order': 3,
        'title': {'en': 'Wildlife Encounter Response Drill', 'ms': 'Latihan Respons Pertemuan Hidupan Liar', 'zh': '野生动物遭遇应对演练'},
        'description': {'en': 'Practise calm crowd control, safe distance, no-feeding messaging, rerouting, and escalation during a wildlife encounter.'},
        'field_brief': {'en': 'A macaque appears near the trail while visitors begin raising phones and snacks.'},
        'learning_objectives': [
            {'en': 'Recognise wildlife stress and visitor risk cues.'},
            {'en': 'Maintain safe distance and prevent feeding.'},
            {'en': 'Decide when to reroute or escalate.'},
        ],
        'success_criteria': [
            {'en': 'Finds animal, visitor, and route-risk hotspots.'},
            {'en': 'Selects a safe non-confrontational response.'},
            {'en': 'Completes the scenario with 70% or higher.'},
        ],
        'panoramas': [
            {
                'name': 'Wildlife encounter zone',
                'panorama_url': TRAINING_IMAGE,
                'hotspots': [
                    {
                        'hotspot_id': 'animal-distance',
                        'title': {'en': 'Safe Distance Zone'},
                        'position_yaw': 28,
                        'position_pitch': 4,
                        'icon_type': 'paw',
                        'color_hint': '#D84315',
                        'content': {
                            'description': {'en': 'Identify the animal position and establish a calm buffer before visitors move closer.'},
                            'visitor_prompt': {'en': 'A visitor says, "Can I get closer? It looks friendly."'},
                            'guide_action': {'en': 'Stop the group, lower voices, and increase distance without sudden movement.'},
                        },
                    },
                    {
                        'hotspot_id': 'feeding-risk',
                        'title': {'en': 'Feeding Risk'},
                        'position_yaw': 150,
                        'position_pitch': -8,
                        'icon_type': 'food-apple-off',
                        'color_hint': '#C62828',
                        'content': {
                            'description': {'en': 'Food exposure can change wildlife behaviour and increase future encounter risk.'},
                            'visitor_prompt': {'en': 'A visitor opens a snack while filming.'},
                            'guide_action': {'en': 'Ask them to pack food away immediately and explain habituation risk.'},
                        },
                    },
                    {
                        'hotspot_id': 'reroute-option',
                        'title': {'en': 'Reroute Decision Point'},
                        'position_yaw': 274,
                        'position_pitch': 0,
                        'icon_type': 'routes',
                        'color_hint': '#6D4C41',
                        'content': {
                            'description': {'en': 'Check whether the safest action is to wait, increase distance, or reroute the group.'},
                            'visitor_prompt': {'en': 'The animal stays on the trail and the group is getting anxious.'},
                            'guide_action': {'en': 'Use the alternate route and report the encounter if the animal remains near visitor flow.'},
                        },
                    },
                ],
            }
        ],
        'quizzes': [
            {
                'question_text': {'en': 'A visitor opens food while a macaque is nearby. What should the guide do first?'},
                'options': {'en': ['Ask them to secure the food and move the group back calmly.', 'Let the animal take it so it leaves.', 'Shout loudly to scare the animal away.', 'Move closer to take a better look.']},
                'correct_option_index': 0,
                'correct_explanation': {'en': 'Correct. Securing food and increasing distance reduces risk without escalating the animal.'},
                'incorrect_explanation': {'en': 'Wildlife response should reduce attractants, distance visitors, and avoid sudden escalation.'},
            }
        ],
    },
]


def ensure_seed_data():
    from .models import ARHotspot, ARPanorama, ARQuizQuestion, ARScenario

    for raw_scenario_data in SCENARIOS:
        scenario_data = raw_scenario_data.copy()
        panoramas = scenario_data.pop('panoramas')
        quizzes = scenario_data.pop('quizzes')
        scenario, _created = ARScenario.objects.update_or_create(
            code=scenario_data['code'],
            defaults={
                **scenario_data,
                'thumbnail': TRAINING_IMAGE,
                'initial_panorama_url': TRAINING_IMAGE,
                'is_published': True,
            },
        )

        for panorama_index, raw_panorama_data in enumerate(panoramas, start=1):
            panorama_data = raw_panorama_data.copy()
            hotspots = panorama_data.pop('hotspots')
            panorama, _created = ARPanorama.objects.update_or_create(
                scenario=scenario,
                order=panorama_index,
                defaults=panorama_data,
            )
            for hotspot_index, hotspot_data in enumerate(hotspots, start=1):
                ARHotspot.objects.update_or_create(
                    panorama=panorama,
                    hotspot_id=hotspot_data['hotspot_id'],
                    defaults={**hotspot_data, 'order': hotspot_index},
                )

        for quiz_index, quiz_data in enumerate(quizzes, start=1):
            ARQuizQuestion.objects.update_or_create(
                scenario=scenario,
                order=quiz_index,
                defaults=quiz_data,
            )
