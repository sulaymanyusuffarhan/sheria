"""Validated foundation forms. Introduced: 2026-11-18; revisions: Git."""

from django import forms
from django.contrib.auth import password_validation
from django.db.models import Q
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .models import Department, Directorate, Role, User
from .policy import DELEGABLE_ROLES


def unit_label(unit):
    return (unit.name_sw or unit.name) if get_language() == 'sw' else unit.name


class SignInForm(forms.Form):
    username = forms.CharField(
        label=_('Username'),
        max_length=150,
        widget=forms.TextInput(
            attrs={'autocomplete': 'username', 'placeholder': _('Enter your username')}
        ),
    )
    password = forms.CharField(
        label=_('Password'),
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'current-password', 'placeholder': _('Enter your password')}
        ),
    )
    code = forms.CharField(
        label=_('Authenticator code'),
        required=False,
        max_length=6,
        widget=forms.TextInput(
            attrs={'autocomplete': 'one-time-code', 'inputmode': 'numeric', 'placeholder': '000000'}
        ),
        help_text=_('Required if multi-factor authentication is enabled for your account.'),
    )

    def clean_username(self):
        return self.cleaned_data['username'].lower()


class EmployeeForm(forms.Form):
    username = forms.RegexField(
        r'^[a-z0-9._-]+$',
        label=_('Username'),
        max_length=80,
        help_text=_('Lowercase letters, numbers, dots, underscores or hyphens.'),
    )
    first_name = forms.CharField(label=_('Given name'), max_length=80)
    last_name = forms.CharField(label=_('Family name'), max_length=80)
    email = forms.EmailField(label=_('Work email'), max_length=254)
    department = forms.ModelChoiceField(label=_('Department'), queryset=Department.objects.none())
    is_active = forms.BooleanField(
        label=_('Active account'),
        required=False,
        initial=True,
        help_text=_('Disabling an account ends its sessions and revokes elevated roles.'),
    )

    def __init__(self, *args, actor, account=None, **kwargs):
        initial = {name: getattr(account, name) for name in self.base_fields} if account else {}
        super().__init__(*args, initial=initial, **kwargs)
        self.account = account
        self.fields['department'].queryset = Department.objects.filter(
            is_active=True, directorate__is_active=True, is_demo=actor.is_demo
        )
        self.fields['department'].label_from_instance = unit_label
        if account:
            del self.fields['username']

    def clean_username(self):
        name = self.cleaned_data['username']
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError(_('That username is already in use.'))
        return name


class DirectoryForm(forms.Form):
    code = forms.RegexField(
        r'^[A-Z0-9-]+$',
        label=_('Unit code'),
        max_length=14,
        help_text=_('Use capital letters and numbers. The code cannot be changed later.'),
    )
    name = forms.CharField(label=_('Name (English)'), max_length=100)
    name_sw = forms.CharField(label=_('Name (Kiswahili)'), max_length=100, required=False)
    directorate = forms.ModelChoiceField(queryset=Directorate.objects.none(), label=_('Directorate'))
    is_active = forms.BooleanField(required=False, label=_('Active'), initial=True)

    def __init__(self, *args, model, actor, unit=None, **kwargs):
        keys = ('code', 'name', 'name_sw', 'is_active')
        initial = {key: getattr(unit, key) for key in keys} if unit else {}
        if unit and model == Department:
            initial['directorate'] = unit.directorate
        super().__init__(*args, initial=initial, **kwargs)
        self.model, self.unit = model, unit
        eligible = Q(is_active=True)
        if unit and model == Department:
            eligible |= Q(pk=unit.directorate_id)
        self.fields['directorate'].queryset = Directorate.objects.filter(eligible, is_demo=actor.is_demo)
        self.fields['directorate'].label_from_instance = unit_label
        if model == Directorate:
            del self.fields['directorate']
        if unit:
            self.fields['code'].disabled = True
            if model == Department:
                self.fields['directorate'].disabled = True

    def clean_code(self):
        code = self.cleaned_data['code']
        if not self.unit and self.model.objects.filter(code=code).exists():
            raise forms.ValidationError(_('That unit code is already in use.'))
        return code


class RequestAccessForm(forms.Form):
    role = forms.ModelChoiceField(
        label=_('Role'), queryset=Role.objects.filter(code__in=DELEGABLE_ROLES).order_by('code')
    )
    department = forms.ModelChoiceField(
        label=_('Department scope'),
        required=False,
        queryset=Department.objects.none(),
        help_text=_(
            'Required for department heads, senior legal officers and records officers. Leave blank for a legal officer.'
        ),
    )
    reason = forms.CharField(
        label=_('Business justification'), max_length=400, widget=forms.Textarea(attrs={'rows': 3})
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = Department.objects.filter(
            is_active=True, directorate__is_active=True, is_demo=actor.is_demo
        )
        self.fields['department'].label_from_instance = unit_label


class DecideAccessForm(forms.Form):
    decision = forms.ChoiceField(
        label=_('Decision'),
        choices=[('approved', _('Approve access')), ('rejected', _('Reject request'))],
    )
    reason = forms.CharField(
        label=_('Decision reason'), max_length=400, widget=forms.Textarea(attrs={'rows': 3})
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('language', 'show_unread_badge')
        labels = {'language': _('Preferred language')}


class ChangePasswordForm(forms.Form):
    current = forms.CharField(
        label=_('Current password'),
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    new = forms.CharField(
        label=_('New password'),
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    confirm = forms.CharField(
        label=_('Confirm new password'),
        max_length=256,
        strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['new'].help_text = _(
            'Use at least 12 characters and avoid common or personal words.'
        )

    def clean_current(self):
        current = self.cleaned_data['current']
        if not self.user.check_password(current):
            raise forms.ValidationError(_('The current password is not correct.'))
        return current

    def clean_new(self):
        value = self.cleaned_data['new']
        password_validation.validate_password(value, self.user)
        return value

    def clean(self):
        data = super().clean()
        if 'new' in data and data.get('confirm') != data['new']:
            self.add_error('confirm', _('The passwords do not match.'))
        if 'current' in data and data.get('new') == data['current']:
            self.add_error('new', _('Choose a different password.'))
        return data
