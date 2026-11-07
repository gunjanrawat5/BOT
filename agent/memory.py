"""Compact, evidence-based exploration memory retained across game resets."""
from collections import Counter


class ExplorationMemory:
    def __init__(self):
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

    def record(self, before, after, plan, run):
        buttons = plan.buttons
        dx = after['player']['x'] - before['player']['x']
        score_delta = after['score'] - before['score']
        health_delta = after['player']['health'] - before['player']['health']
        p, c = after['player'], after['canvas']
        if 'left' in buttons and p['x'] <= 1e-6:
            self.edges.add('left')
        if 'right' in buttons and p['x'] + p['width'] >= c['width'] - 1e-6:
            self.edges.add('right')
        self.steps += 1
        self.categories[plan.category] += 1
        self.deaths += int(before['status'] != 'game_over' and after['status'] == 'game_over')
        self.best_score = max(self.best_score, after['score'])
        self.longest_run_ticks = max(self.longest_run_ticks, after['tick'])
        self.score_gains += int(score_delta > 0)
        self.damage_events += int(health_delta < 0)
        self.shooting_batches += int('shoot' in buttons)
        self.moving_shooting_batches += int('shoot' in buttons and bool({'left', 'right'} & set(buttons)))
        before_ids = {e['id'] for e in before['enemies'] if 'id' in e}
        after_ids = {e['id'] for e in after['enemies'] if 'id' in e}
        outcome = {'run': run, 'plan': plan.model_dump(), 'start_x': before['player']['x'],
                   'end_x': p['x'], 'movement_x': dx, 'score_delta': score_delta,
                   'health_delta': health_delta, 'enemy_count_before': len(before['enemies']),
                   'enemy_count_after': len(after['enemies']), 'enemy_ids_disappeared': sorted(before_ids - after_ids, key=str),
                   'projectiles_before': len(before['projectiles']), 'projectiles_after': len(after['projectiles']),
                   'tick': after['tick'], 'status': after['status']}
        self.recent = (self.recent + [outcome])[-5:]
        return outcome

    def summary(self):
        goals = []
        if not self.score_gains:
            goals.append('Earn a score increase: locate an enemy, align the ship horizontally and fire; reassess the result.')
        if not self.moving_shooting_batches:
            goals.append('Test firing while steering toward a target or away from a visible threat.')
        goals.append('Survive longer: observe approaching enemies/projectiles and adapt direction and action duration.')
        if not self.damage_events:
            goals.append('Observe a controlled collision/health interaction after establishing movement and shooting.')
        return {'experiments': self.steps, 'category_counts': dict(self.categories),
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
        if plan.category == 'boundary' and len(self.recent) >= 2 and all(r['plan']['category'] == 'boundary' for r in self.recent[-2:]):
            return 'Two consecutive boundary experiments already ran. Choose a gameplay interaction in another category.'
        return None
