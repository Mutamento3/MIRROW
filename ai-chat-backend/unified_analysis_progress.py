"""Body-free display of actual stages; does not schedule or persist domain work."""
from datetime import datetime
LABELS = {'auxiliary': '收藏与纪念日', 'preparation': '原料与记忆准备', 'world': '世界书', 'self': '自我认知', 'interest': '兴趣爱好', 'other': '他者书', 'brief': '认知摘要'}

class StageProgress:

    def __init__(self, publish):
        self.publish = publish
        self.stages = {name: {'name': name, 'label': label, 'status': 'pending'} for name, label in LABELS.items()}
        self._publish()

    def _publish(self):
        self.publish([dict(stage) for stage in self.stages.values()])

    def report(self, name, status, *, reused=False):
        if name not in self.stages:
            return
        stage = self.stages[name]
        now = datetime.now().isoformat()
        stage.update(status=status, reused=reused)
        if status == 'running':
            stage['started_at'] = now
        else:
            stage['completed_at'] = now
        self._publish()

    def finish(self, outcome):
        for stage in self.stages.values():
            if stage['status'] == 'running':
                stage.update(status=outcome, completed_at=datetime.now().isoformat())
            elif stage['status'] == 'pending':
                stage['status'] = 'not_started'
        self._publish()
