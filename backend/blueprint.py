import os
from datetime import datetime
import logging

from pathlib import Path

from sqlalchemy.orm.exc import NoResultFound
from flask import (
    Blueprint,
    request,
    current_app,
    send_from_directory,
    Response,
    render_template,
    jsonify
)
from flask_cors import cross_origin
from geonature.utils.utilssqlalchemy import (
    json_resp, to_json_resp, to_csv_resp
)

from geonature.utils.filemanager import (
    removeDisallowedFilenameChars, delete_recursively)
from pypnusershub.db.tools import InsufficientRightsError
from geonature.core.gn_permissions import decorators as permissions

from .repositories import ExportRepository, EmptyDataSetError, generate_swagger_spec

from flask_admin.contrib.sqla import ModelView
from .models import Export, CorExportsRoles
from pypnnomenclature.admin import admin
from geonature.utils.env import DB

logger = current_app.logger
logger.setLevel(logging.DEBUG)
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
# current_app.config['DEBUG'] = True

blueprint = Blueprint('exports', __name__)
blueprint.template_folder = os.path.join(blueprint.root_path, 'templates')
blueprint.static_folder = os.path.join(blueprint.root_path, 'static')
repo = ExportRepository()


"""
#################################################################
    Configuration de l'admin
#################################################################
"""
# FIX: remove init Export model
admin.add_view(ModelView(Export, DB.session))
admin.add_view(ModelView(CorExportsRoles, DB.session))

EXPORTS_DIR = os.path.join(current_app.static_folder, 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)
SHAPEFILES_DIR = os.path.join(current_app.static_folder, 'shapefiles')
MOD_CONF_PATH = os.path.join(blueprint.root_path, os.pardir, 'config')

# HACK when install the module, the config of the module is not yet available
# we cannot use current_app.config['EXPORT']
try:
    MOD_CONF = current_app.config['EXPORTS']
    API_URL = MOD_CONF['MODULE_URL']
except KeyError:
    API_URL = ''

ASSETS = os.path.join(blueprint.root_path, 'assets')

"""
#################################################################
    Configuration de swagger
#################################################################
"""


@blueprint.route('/swagger/')
@blueprint.route('/swagger/<int:id_export>', methods=['GET'])
def swagger_ui(id_export=None):
    """
        Génération de l'interface de swagger
    """
    if not id_export:
        id_export = ""

    return render_template(
        'index.html',
        API_ENDPOINT=API_URL,
        id_export=id_export
    )

@blueprint.route('/swagger-ressources/', methods=['GET'])
@blueprint.route('/swagger-ressources/<int:id_export>', methods=['GET'])
def swagger_ressources(id_export=None):
    """
        Génération des spécifications swagger
    """

    # return jsonify(swagger_example)
    if not id_export:
        swagger_spec = render_template('/swagger/main_swagger_doc.json')
        return Response(swagger_spec)


    # Si l'id export exist et que les droits sont définis
    try:
        export = Export.query.filter(Export.id == id_export).one()
    except (NoResultFound, EmptyDataSetError):
        return jsonify({"message": "no export with this id"}), 404

    # Si un fichier de surcouche est défini
    file_name = 'api_specification_' + str(id_export) + '.json'
    path = Path(blueprint.template_folder, 'swagger', file_name)

    if path.is_file():
        swagger_spec = render_template('/swagger/' + file_name)
        return Response(swagger_spec)

    # Génération automatique des spécification
    export_parameters = generate_swagger_spec(id_export)

    swagger_spec = render_template(
        '/swagger/generic_swagger_doc.json',
        export_nom=export.label,
        export_description=export.desc,
        export_path="{}/api/{}".format(API_URL, id_export),
        export_parameters=export_parameters
    )

    return Response(swagger_spec)



def export_filename(export):
    return '{}_{}'.format(
        removeDisallowedFilenameChars(export.get('label')),
        datetime.now().strftime('%Y_%m_%d_%Hh%Mm%S'))


"""
#################################################################
    Configuration des routes qui permettent de réaliser les exports
#################################################################
"""


@blueprint.route('/<int:id_export>/<export_format>', methods=['GET'])
@cross_origin(
    supports_credentials=True,
    allow_headers=['content-type', 'content-disposition'],
    expose_headers=['Content-Type', 'Content-Disposition', 'Authorization'])
@permissions.check_cruved_scope(
    'E', True, module_code='EXPORTS',
    redirect_on_expiration=current_app.config.get('URL_APPLICATION'),
    redirect_on_invalid_token=current_app.config.get('URL_APPLICATION')
    )
def getOneExport(id_export, export_format, info_role):
    if (
        id_export < 1
        or
        export_format not in blueprint.config.get('export_format_map')
    ):
        return to_json_resp({'api_error': 'InvalidExport'}, status=404)

    current_app.config.update(
        export_format_map=blueprint.config['export_format_map']
    )

    filters = {f: request.args.get(f) for f in request.args}

    try:
        export, columns, data = repo.get_by_id(
            info_role, id_export, with_data=True, export_format=export_format,
            filters=filters, limit=10000, offset=0
        )

        if export:
            fname = export_filename(export)
            has_geometry = export.get('geometry_field', None)

            if export_format == 'json':
                return to_json_resp(
                    data.get('items'),
                    as_file=True,
                    filename=fname,
                    indent=4)

            if export_format == 'csv':
                return to_csv_resp(
                    fname,
                    data.get('items'),
                    [c.name for c in columns],
                    separator=',')

            if (export_format == 'shp' and has_geometry):
                from geoalchemy2.shape import from_shape
                from shapely.geometry import asShape
                from geonature.utils.utilsgeometry import FionaShapeService as ShapeService  # noqa: E501

                delete_recursively(
                    SHAPEFILES_DIR, excluded_files=['.gitkeep'])

                ShapeService.create_shapes_struct(
                    db_cols=columns, srid=export.get('geometry_srid'),
                    dir_path=SHAPEFILES_DIR, file_name=''.join(['export_', fname]))  # noqa: E501

                items = data.get('items')

                for feature in items['features']:
                    geom, props = (feature.get(field)
                                   for field in ('geometry', 'properties'))

                    ShapeService.create_feature(
                            props, from_shape(
                                asShape(geom), export.get('geometry_srid')))

                ShapeService.save_and_zip_shapefiles()

                return send_from_directory(
                    SHAPEFILES_DIR, ''.join(['export_', fname, '.zip']),
                    as_attachment=True)

            else:
                return to_json_resp(
                    {'api_error': 'NonTransformableError'}, status=404)

    except NoResultFound as e:
        return to_json_resp(
            {'api_error': 'NoResultFound',
             'message': str(e)}, status=404)
    except InsufficientRightsError:
        return to_json_resp(
            {'api_error': 'InsufficientRightsError'}, status=403)
    except EmptyDataSetError as e:
        return to_json_resp(
            {'api_error': 'EmptyDataSetError',
             'message': str(e)}, status=404)
    except Exception as e:
        logger.critical('%s', e)
        if current_app.config['DEBUG']:
            raise
        return to_json_resp({'api_error': 'LoggedError'}, status=400)


@blueprint.route('/', methods=['GET'])
@permissions.check_cruved_scope(
    'R', True, module_code='EXPORTS',
    redirect_on_expiration=current_app.config.get('URL_APPLICATION'),
    redirect_on_invalid_token=current_app.config.get('URL_APPLICATION')
    )
@json_resp
def getExports(info_role):
    """
        Fonction qui renvoie la liste des exports
        accessible pour un role donné
    """
    try:
        exports = repo.getAllowedExports(info_role)
    except NoResultFound:
        return {'api_error': 'NoResultFound',
                'message': 'Configure one or more export'}, 404
    except Exception as e:
        logger.critical('%s', str(e))
        return {'api_error': 'LoggedError'}, 400
    else:
        return [export.as_dict() for export in exports]


@blueprint.route('/etalab', methods=['GET'])
def etalab_export():
    if not blueprint.config.get('etalab_export'):
        return to_json_resp(
            {'api_error': 'EtalabDisabled',
             'message': 'Etalab export is disabled'}, status=501)

    from datetime import time
    from geonature.utils.env import DB
    from geonature.utils.utilssqlalchemy import GenericQuery
    from .rdf import OccurrenceStore

    conf = current_app.config.get('EXPORTS')
    export_etalab = conf.get('etalab_export')
    seeded = False
    if os.path.isfile(export_etalab):
        seeded = True
        midnight = datetime.combine(datetime.today(), time.min)
        mtime = datetime.fromtimestamp(os.path.getmtime(export_etalab))
        ts_delta = mtime - midnight

    if not seeded or ts_delta.total_seconds() < 0:
        store = OccurrenceStore()
        query = GenericQuery(
            DB.session, 'export_occtax_sinp', 'pr_occtax',
            geometry_field=None, filters=[]
        )
        data = query.return_query()
        for record in data.get('items'):
            event = store.build_event(record)
            obs = store.build_human_observation(event, record)
            store.build_location(obs, record)
            occurrence = store.build_occurrence(event, record)
            organism = store.build_organism(occurrence, record)
            identification = store.build_identification(organism, record)
            store.build_taxon(identification, record)
        try:
            with open(export_etalab, 'w+b') as xp:
                store.save(store_uri=xp)
        except FileNotFoundError as e:
            response = Response(
                response="FileNotFoundError : {}".format(
                    export_etalab
                ),
                status=500,
                mimetype='application/json'
            )
            return response

    return send_from_directory(
        os.path.dirname(export_etalab), os.path.basename(export_etalab)
    )


@blueprint.route('/api/<int:id_export>', methods=['GET'])
@permissions.check_cruved_scope(
    'R', True, module_code='EXPORTS',
    redirect_on_expiration=current_app.config.get('URL_APPLICATION'),
    redirect_on_invalid_token=current_app.config.get('URL_APPLICATION')
)
@json_resp
def get_one_export_api(id_export, info_role):
    """
        Fonction qui expose les exports disponibles à un role
            sous forme d'api

        Le requetage des données se base sur la classe GenericQuery qui permet
            de filter les données de façon dynamique en respectant des
            conventions de nommage

        Parameters
        ----------
        limit : nombre limit de résultats à retourner
        offset : numéro de page

        FILTRES :
            nom_col=val: Si nom_col fait partie des colonnes
                de la vue alors filtre nom_col=val
            ilikenom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type texte
                alors filtre nom_col ilike '%val%'
            filter_d_up_nom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type date
                alors filtre nom_col >= val
            filter_d_lo_nom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type date
                alors filtre nom_col <= val
            filter_d_eq_nom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type date
                alors filtre nom_col == val
            filter_n_up_nom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type numérique
                alors filtre nom_col >= val
            filter_n_lo_nom_col=val: Si nom_col fait partie des colonnes
                de la vue et que la colonne est de type numérique
                alors filtre nom_col <= val
        ORDONNANCEMENT :
            orderby: char
                Nom du champ sur lequel baser l'ordonnancement
            order: char (asc|desc)
                Sens de l'ordonnancement

        Returns
        -------
        json
        {
            'total': Nombre total de résultat,
            'total_filtered': Nombre total de résultat après filtration,
            'page': Numéro de la page retournée,
            'limit': Nombre de résultats,
            'items': données au format Json ou GeoJson
        }


            order by : @TODO
    """
    limit = request.args.get('limit', default=1000, type=int)
    offset = request.args.get('offset', default=0, type=int)

    args = request.args.to_dict()
    if "limit" in args:
        args.pop("limit")
    if "offset" in args:
        args.pop("offset")
    filters = {f: args.get(f) for f in args}

    current_app.config.update(
        export_format_map=blueprint.config['export_format_map']
    )

    export, columns, data = repo.get_by_id(
        info_role, id_export, with_data=True, export_format='json',
        filters=filters, limit=limit, offset=offset
    )
    return data
