from django import forms


class SoulRegistrationStartForm(forms.Form):
    email = forms.EmailField()


class SoulOfferingUploadForm(forms.Form):
    offering = forms.FileField()
