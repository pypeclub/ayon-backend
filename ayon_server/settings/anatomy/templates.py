from pydantic import validator

from ayon_server.settings.common import BaseSettingsModel
from ayon_server.settings.settings_field import SettingsField
from ayon_server.settings.validators import ensure_unique_names, normalize_name


class BaseTemplate(BaseSettingsModel):
    name: str = SettingsField(..., title="Name")

    @validator("name")
    def validate_name(cls, value):
        return normalize_name(value)


class WorkTemplate(BaseTemplate):
    directory: str = SettingsField(..., title="Directory template")
    file: str = SettingsField(..., title="File name template")


class PublishTemplate(BaseTemplate):
    directory: str = SettingsField(..., title="Directory template")
    file: str = SettingsField(..., title="File name template")


class HeroTemplate(BaseTemplate):
    directory: str = SettingsField(..., title="Directory template")
    file: str = SettingsField(..., title="File name template")


class DeliveryTemplate(BaseTemplate):
    directory: str = SettingsField(..., title="Directory template")
    file: str = SettingsField(..., title="File name template")


class CustomTemplate(BaseTemplate):
    _layout = "compact"
    value: str = SettingsField("", title="Template value")


class StagingDirectory(BaseTemplate):
    _layout = "compact"
    directory: str = SettingsField("")


# TODO: Custom templates are not supported yet
# data: dict[str, str] = Field(default_factory=dict)


class Templates(BaseSettingsModel):
    version_padding: int = SettingsField(
        default=3,
        title="Version padding",
        gt=0,
    )

    version: str = SettingsField(
        default="v{version:0>{@version_padding}}",
        title="Version template",
    )

    frame_padding: int = SettingsField(
        default=4,
        title="Frame padding",
        gt=0,
    )

    frame: str = SettingsField(
        default="{frame:0>{@frame_padding}}",
        title="Frame template",
    )

    work: list[WorkTemplate] = SettingsField(
        default_factory=lambda: [
            WorkTemplate(
                name="default",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/work/{task[name]}",  # noqa: E501
                file="{project[code]}_{folder[name]}_{task[name]}_{@version}<_{comment}>.{ext}",  # noqa: E501
            ),
            WorkTemplate(
                name="unreal",
                directory="{root[work]}/{project[name]}/unreal/{task[name]}",
                file="{project[code]}_{folder[name]}.{ext}",
            ),
        ],
        title="Work",
    )

    publish: list[PublishTemplate] = SettingsField(
        title="Publish",
        default_factory=lambda: [
            PublishTemplate(
                name="default",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{product[name]}/{@version}",
                file="{project[code]}_{folder[name]}_{product[name]}_{@version}<_{output}><.{@frame}><_{udim}>.{ext}",
            ),
            PublishTemplate(
                name="render",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{product[name]}/{@version}",
                file="{project[code]}_{folder[name]}_{product[name]}_{@version}<_{output}><.{@frame}>.{ext}",
            ),
            PublishTemplate(
                name="online",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{product[name]}/{@version}",
                file="{originalBasename}<.{@frame}><_{udim}>.{ext}",
            ),
            PublishTemplate(
                name="source",
                directory="{root[work]}/{originalDirname}",
                file="{originalBasename}.{ext}",
            ),
            PublishTemplate(
                name="maya2unreal",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}",
                file="{product[name]}_{@version}<_{output}><.{@frame}>.{ext}",
            ),
            PublishTemplate(
                name="simpleUnrealTextureHero",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/hero",
                file="{originalBasename}.{ext}",
            ),
            PublishTemplate(
                name="simpleUnrealTexture",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{@version}",
                file="{originalBasename}_{@version}.{ext}",
            ),
            PublishTemplate(
                name="unrealuasset",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{product[name]}/{@version}",
                file="{originalBasename}.{ext}",
            ),
        ],
    )

    hero: list[HeroTemplate] = SettingsField(
        title="Hero",
        default_factory=lambda: [
            HeroTemplate(
                name="default",
                directory="{root[work]}/{project[name]}/{hierarchy}/{folder[name]}/publish/{product[type]}/{product[name]}/hero",  # noqa: E501
                file="{project[code]}_{folder[name]}_{product[name]}_hero<_{output}><.{@frame}><_{udim}>.{ext}",  # noqa: E501
            )
        ],
    )

    delivery: list[DeliveryTemplate] = SettingsField(
        default_factory=list,
        title="Delivery",
    )

    staging: list[StagingDirectory] = SettingsField(
        default_factory=list,
        title="Staging directories",
    )

    others: list[CustomTemplate] = SettingsField(
        default_factory=list,
        title="Others",
    )

    @validator("work", "publish", "hero", "delivery", "others", "staging")
    def validate_template_group(cls, value):
        ensure_unique_names(value)
        return value

    @validator("work", "publish", "hero")
    def validate_has_default(cls, value):
        for template in value:
            if template.name == "default":
                break
        else:
            raise ValueError("Default template must be defined")
        return value
