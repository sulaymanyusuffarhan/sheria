from django import forms
from django.utils.translation import gettext_lazy as _

from casework.forms import PreparedFileField, RevisionForm, attest, day, text
from casework.models import Document
from casework.policy import assignee, current_assignment, head

from .models import Item


class VersionForm(RevisionForm):
    provenance = text('Source and provenance', 600, rows=4)
    document = PreparedFileField('Private document')


class ItemForm(VersionForm):
    title = text('Document title')
    category = forms.ChoiceField(label=_('Category'), choices=[('', '—'), *Item.Category.choices])
    tags = text('Search tags', 160, required=False)
    retain_until = day('Retention review date', required=False)
    sealed = attest(
        'Restrict this item to the scoped Head and the actively assigned legal officer.', required=False
    )

    def __init__(self, *args, policy, **kwargs):
        super().__init__(*args, **kwargs)
        if not (head(policy) or assignee(policy, current_assignment(self.case))):
            self.fields.pop('sealed')
        self.order_fields(
            ('revision', 'title', 'category', 'tags', 'retain_until', 'sealed', 'provenance', 'document')
        )


class CustodyForm(RevisionForm):
    reason = text('Custody reason', 900, rows=4)


class RescanForm(RevisionForm):
    class Pending(forms.ModelChoiceField):
        def label_from_instance(self, obj):
            return f'v{obj.number} · {obj.document.name}'

    pending = Pending(queryset=None, label=_('Quarantined version'))

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pending'].queryset = item.versions.filter(
            document__scan=Document.Scan.PENDING
        ).select_related('document')
