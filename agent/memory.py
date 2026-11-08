"""Compact, evidence-based exploration memory retained across game resets."""
from collections import Counter
from .profiles import get_profile, player_score


class ExplorationMemory:
    def __init__(self, profile='space_shooter'):
        self.profile = get_profile(profile)
        self.paddle_contacts = 0
        self.best_rally = 0
        self.categories = Counter()
        self.edges = set()
        self.steps = 0
        self.deaths = 0
        self.best_score = 0
        self.longest_run_ticks = 0
        self.score_gains = 0
        self.damage_events = 0
        self.shooting_batches = 0
        self.moving_shooting_batches = 0
        self.recent = []

    def record(self, before, after, plan, run, timeline=None):
        buttons = plan.buttons
        dx = after['player']['x'] - before['player']['x']
        score_delta = player_score(after) - player_score(before)
        health_delta = after['player']['health'] - before['player']['health'] if 'health' in after['player'] and 'health' in before['player'] else None
        p, c = after['player'], after['canvas']
        if 'left' in buttons and p['x'] <= 1e-6:
            self.edges.add('left')
        if 'right' in buttons and p['x'] + p['width'] >= c['width'] - 1e-6:
            self.edges.add('right')
        if self.profile.id == 'ping_pong':
            if 'up' in buttons and p['y'] <= 1e-6: self.edges.add('top')
            if 'down' in buttons and p['y'] + p['height'] >= c['height'] - 1e-6: self.edges.add('bottom')
            self.best_rally = max(self.best_rally, after.get('rally', 0))
            self.paddle_contacts += sum(event['type']=='paddle_hit' and event['data'].get('paddle_id')=='player' for event in (timeline or {}).get('events', []))
        self.steps += 1
        self.categories[plan.category] += 1
        self.deaths += int(before['status'] != 'game_over' and after['status'] == 'game_over')
        self.best_score = max(self.best_score, player_score(before), player_score(after))
        self.longest_run_ticks = max(self.longest_run_ticks, after['tick'])
        self.score_gains += int(score_delta > 0)
        self.damage_events += int(health_delta is not None and health_delta < 0)
        self.shooting_batches += int('shoot' in buttons)
        self.moving_shooting_batches += int('shoot' in buttons and bool({'left', 'right'} & set(buttons)))
        before_ids = {e['id'] for e in before.get('enemies', []) if 'id' in e}
        after_ids = {e['id'] for e in after.get('enemies', []) if 'id' in e}
        outcome = {'run': run, 'plan': plan.model_dump(), 'start_x': before['player']['x'],
                   'end_x': p['x'], 'movement_x': dx, 'score_delta': score_delta,
                   'health_delta': health_delta, 'enemy_count_before': len(before.get('enemies', [])),
                   'enemy_count_after': len(after.get('enemies', [])), 'enemy_ids_disappeared': sorted(before_ids - after_ids, key=str),
                   'projectiles_before': len(before.get('projectiles', [])), 'projectiles_after': len(after.get('projectiles', [])),
                   'tick': after['tick'], 'status': after['status']}
        outcome['score'] = player_score(after)
        if self.profile.id == 'ping_pong':
            outcome.update(start_y=before['player']['y'], end_y=p['y'], movement_y=p['y']-before['player']['y'],
                           opponent_score=after['score']['cpu'], ball=after['ball'], rally=after['rally'])
            for key in ('health_delta', 'enemy_count_before', 'enemy_count_after', 'enemy_ids_disappeared', 'projectiles_before', 'projectiles_after'):
                outcome.pop(key, None)
        self.recent = (self.recent + [outcome])[-5:]
        return outcome

    def summary(self):
        if self.profile.id == 'ping_pong':
            return {'actions': self.steps, 'category_counts': dict(self.categories),
                    'edges_already_reached': sorted(self.edges), 'best_score': self.best_score,
                    'game_overs': self.deaths, 'best_rally': self.best_rally, 'player_paddle_contacts': self.paddle_contacts,
                    'open_goals': ['Intercept approaching balls using position and velocity; adjust action duration near contact.',
                                   'Sustain rallies, vary contact position and try to win points.',
                                   'Investigate wall rebounds, point attribution, serves and pause/resume when evidence warrants.'],
                    'interpretation': 'Player and CPU points are separate. Missing intermediate events are unknown; tick can advance while paused.'}
        goals = []
        if not self.score_gains:
            goals.append('Earn a score increase: locate an enemy, align the ship horizontally and fire; reassess the result.')
        if not self.moving_shooting_batches:
            goals.append('Test firing while steering toward a target or away from a visible threat.')
        goals.append('Survive longer: observe approaching enemies/projectiles and adapt direction and action duration.')
        if not self.damage_events:
            goals.append('Observe a controlled collision/health interaction after establishing movement and shooting.')
        return {'actions': self.steps, 'category_counts': dict(self.categories),
                'edges_already_reached': sorted(self.edges), 'best_score': self.best_score,
                'longest_run_ticks': self.longest_run_ticks, 'game_overs': self.deaths,
                'score_increase_events': self.score_gains, 'health_loss_events': self.damage_events,
                'shooting_batches': self.shooting_batches, 'moving_shooting_batches': self.moving_shooting_batches,
                'open_goals': goals,
                'interpretation': 'These are observations, not confirmed mechanics or defects. Disappearing enemies do not alone prove a kill.'}

    def repetition_reason(self, plan, state):
        buttons = set(plan.buttons)
        p = state['player']
        if buttons == {'left'} and p['x'] <= 1e-6 and 'left' in self.edges:
            return 'Left edge already reached; holding left here repeats a settled boundary observation.'
        if buttons == {'right'} and p['x'] + p['width'] >= state['canvas']['width'] - 1e-6 and 'right' in self.edges:
            return 'Right edge already reached; holding right here repeats a settled boundary observation.'
        if self.profile.id == 'ping_pong' and plan.category == 'boundary':
            if buttons == {'up'} and p['y']<=1e-6 and 'top' in self.edges: return 'Top paddle boundary already reached; choose a different interaction.'
            if buttons == {'down'} and p['y']+p['height']>=state['canvas']['height']-1e-6 and 'bottom' in self.edges: return 'Bottom paddle boundary already reached; choose a different interaction.'
        if plan.category == 'boundary' and len(self.recent) >= 2 and all(r['plan']['category'] == 'boundary' for r in self.recent[-2:]):
            return 'Two consecutive boundary actions already ran. Choose a gameplay interaction in another category.'
        return None
