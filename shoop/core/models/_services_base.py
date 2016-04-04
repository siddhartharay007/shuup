# This file is part of Shoop.
#
# Copyright (c) 2012-2016, Shoop Ltd. All rights reserved.
#
# This source code is licensed under the AGPLv3 license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import unicode_literals

import functools
import random

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import ugettext_lazy as _
from parler.managers import TranslatableQuerySet
from parler.models import TranslatedField
from polymorphic.models import PolymorphicModel

from shoop.core.fields import InternalIdentifierField
from shoop.core.pricing import PriceInfo

from ._base import ShoopModel, TranslatableShoopModel
from ._product_shops import ShopProduct


class ServiceQuerySet(TranslatableQuerySet):
    def enabled(self):
        no_provider_filter = {
            self.model.provider_attr: None,
        }
        enabled_filter = {
            self.model.provider_attr + '__enabled': True,
            'enabled': True,
        }
        return self.exclude(**no_provider_filter).filter(**enabled_filter)

    def available_ids(self, shop, products):
        """
        Retrieve common available services for a shop and product IDs.

        :param shop_id: Shop ID
        :type shop_id: int
        :param product_ids: Product IDs
        :type product_ids: set[int]
        :return: Set of service IDs
        :rtype: set[int]
        """
        shop_product_m2m = self.model.shop_product_m2m
        shop_product_limiter_attr = "limit_%s" % self.model.shop_product_m2m

        limiting_products_query = {
            "shop": shop,
            "product__in": products,
            shop_product_limiter_attr: True
        }

        available_ids = set(self.enabled().values_list("pk", flat=True))

        for shop_product in ShopProduct.objects.filter(**limiting_products_query):
            available_ids &= set(getattr(shop_product, shop_product_m2m).values_list("pk", flat=True))
            if not available_ids:  # Out of IDs, better just fail fast
                break

        return available_ids

    def available(self, shop, products):
        return self.filter(pk__in=self.available_ids(shop, products))


class Service(TranslatableShoopModel):
    identifier = InternalIdentifierField(unique=True)
    enabled = models.BooleanField(default=True, verbose_name=_("enabled"))

    # Initialized from ServiceChoice.identifier
    choice_identifier = models.CharField(blank=True, max_length=64)

    name = TranslatedField()

    tax_class = models.ForeignKey(
        'TaxClass', verbose_name=_("tax class"), on_delete=models.PROTECT)

    objects = ServiceQuerySet.as_manager()

    behavior_components = models.ManyToManyField('ServiceBehaviorComponent')

    class Meta:
        abstract = True

    @property
    def provider(self):
        """
        :rtype: shoop.core.models.ServiceProvider
        """
        return getattr(self, self.provider_attr)

    def get_checkout_phase(self, **kwargs):
        """
        :rtype: shoop.core.front.checkout.CheckoutPhaseViewMixin|None
        """
        return self.provider.get_checkout_phase(service=self, **kwargs)

    def get_effective_name(self, source):
        return self.name  # TODO(SHOOP-2293): Do we need Service.get_effective_name?

    def is_available_for(self, source):
        """
        Return true if service is available for given source.

        :type source: shoop.core.order_creator.OrderSource
        :rtype: bool
        """
        return not any(self.get_unavailability_reasons(source))

    def get_unavailability_reasons(self, source):
        """
        Get reasons of being unavailable for given source.

        :type source: shoop.core.order_creator.OrderSource
        :rtype: Iterable[ValidationError]
        """
        if not self.provider or not self.provider.enabled or not self.enabled:
            yield ValidationError(_("%s is disabled") % self, code='disabled')

        if source.shop != self.provider.shop:
            yield ValidationError(
                _("%s is for different shop") % self, code='wrong_shop')

        for component in self.behavior_components.all():
            for reason in component.get_unavailability_reasons(self, source):
                yield reason

    def get_total_cost(self, source):
        """
        Get total cost of this service for items in given source.

        :type source: shoop.core.order_creator.OrderSource
        :rtype: PriceInfo
        """
        price_infos = (x.price_info for x in self.get_costs(source))
        zero = source.create_price(0)
        return _sum_price_infos(price_infos, PriceInfo(zero, zero, quantity=1))

    def get_costs(self, source):
        """
        Get costs of this service for items in given source.

        :type source: shoop.core.order_creator.OrderSource
        :return: description, price and tax class of the costs
        :rtype: Iterable[Cost]
        """
        for component in self.behavior_components.all():
            for cost in component.get_costs(self, source):
                desc = (cost.description or component.get_name(self, source))
                tax_class = (cost.tax_class or self.tax_class)
                yield Cost(cost.price, desc, tax_class, cost.base_price)

    def get_lines(self, source):
        """
        Get lines for given source.

        :type source: shoop.core.order_creator.OrderSource
        :rtype: Iterable[shoop.core.order_creator.SourceLine]
        """
        line_prefix = type(self).__name__.lower()

        def rand_int():
            return random.randint(0, 0x7FFFFFFF)

        costs = (
            list(self.get_costs(source)) or
            [Cost(source.create_price(0), self.name, self.tax_class)])
        for (n, cost) in enumerate(costs):
            price_info = cost.price_info
            yield source.create_line(
                line_id="%s_%02d_%x" % (line_prefix, n, rand_int()),
                type=self.line_type,
                quantity=cost.price_info.quantity,
                text=cost.description,
                base_unit_price=price_info.base_unit_price,
                discount_amount=price_info.discount_amount,
                tax_class=cost.tax_class,
            )

    def _make_sure_is_usable(self):
        if not self.provider:
            raise ValueError('%r has no %s' % (self, self.provider_attr))
        if not self.enabled:
            raise ValueError('%r is disabled' % (self,))
        if not self.provider.enabled:
            raise ValueError(
                '%s of %r is disabled' % (self.provider_attr, self))


def _sum_price_infos(price_infos, zero):
    def plus(pi1, pi2):
        assert pi1.quantity == pi2.quantity
        return PriceInfo(
            pi1.price + pi2.price,
            pi1.base_price + pi2.base_price,
            quantity=pi1.quantity,
        )
    return functools.reduce(plus, price_infos, zero)


class ServiceBehaviorComponent(ShoopModel, PolymorphicModel):
    #: Name for the component (lazy translated)
    name = None

    #: Help text for the component (lazy translated)
    help_text = None

    def __init__(self, *args, **kwargs):
        if type(self) != ServiceBehaviorComponent and self.name is None:
            raise TypeError('%s.name is not defined' % type(self).__name__)
        super(ServiceBehaviorComponent, self).__init__(*args, **kwargs)

    def get_name(self, service, source):
        """
        :type service: Service
        :type source: shoop.core.order_creator.OrderSource
        :rtype: str
        """
        return ""

    def get_unavailability_reasons(self, service, source):
        """
        :type service: Service
        :type source: shoop.core.order_creator.OrderSource
        :rtype: Iterable[ValidationError]
        """
        return ()

    def get_costs(self, service, source):
        """
        TODO: Document!

        Costs may be created with the `create_cost` function.

        :type service: Service
        :type source: shoop.core.order_creator.OrderSource
        :rtype: Iterable[Cost]
        """
        return ()

    def get_delivery_time(self, service, source):
        """
        :type service: Service
        :type source: shoop.core.order_creator.OrderSource
        :rtype: shoop.utils.dates.DurationRange|None
        """
        return None

    @classmethod
    def create_cost(cls, price, description=None,
                    tax_class=None, base_price=None):
        return Cost(price, description, tax_class, base_price)


class Cost(object):
    def __init__(
            self, price, description=None,
            tax_class=None, base_price=None):
        """
        :type price: shoop.core.pricing.Price
        :type description: str
        :type tax_class: shoop.core.models.TaxClass|None
        :type base_price: shoop.core.pricing.Price|None
        """
        self.price = price
        self.description = description
        self.tax_class = tax_class
        self.base_price = base_price if base_price is not None else price

    @property
    def price_info(self):
        return PriceInfo(self.price, self.base_price, quantity=1)
