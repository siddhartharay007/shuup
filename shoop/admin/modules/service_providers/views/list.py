# -*- coding: utf-8 -*-
# This file is part of Shoop.
#
# Copyright (c) 2012-2016, Shoop Ltd. All rights reserved.
#
# This source code is licensed under the AGPLv3 license found in the
# LICENSE file in the root directory of this source tree.
from __future__ import unicode_literals

from django.utils.translation import ugettext_lazy as _

from shoop.admin.utils.picotable import Column, TextFilter
from shoop.admin.utils.views import PicotableListView
from shoop.core.models import Carrier, PaymentProcessor


class _BaseServiceProviderListView(PicotableListView):
    model = None  # Overridden below
    columns = [
        Column(
            "name",
            _(u"Name"),
            sort_field="translations__name",
            filter_config=TextFilter(
                filter_field="name",
                placeholder=_("Filter by name...")
            )
        )
    ]

    def get_object_abstract(self, instance, item):
        return [
            {"text": "%s" % instance, "class": "header"},
        ]


class CarrierListView(_BaseServiceProviderListView):
    model = Carrier


class PaymentProcessorListView(_BaseServiceProviderListView):
    model = PaymentProcessor
