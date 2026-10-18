"""Handwritten executor fixtures, never supplied to live synthesis."""
import json

from test_compiler import canvas_family, family_for

from conduit.programs import Program


def javascript_family(base, field="title"):
    family = family_for(base, field)
    for verb, method in {"create": "POST", "read": "GET", "update": "PATCH", "delete": "DELETE"}.items():
        url = json.dumps(base) + ('' if verb == "create" else ' + "/" + encodeURIComponent(input.id)')
        body = 'input' if verb == "create" else json.dumps(field) + ': input[' + json.dumps(field) + ']'
        body = '{' + body + '}' if verb == "update" else body
        options = "" if verb == "read" else ', headers:{"x-csrf-token":api.session({kind:"cookie",key:"csrf"})}'
        if verb in ("create", "update"):
            options += ', body:' + body
        options += ', accept:[200,201,204,404]'
        family.operations[verb].program = Program(javascript=(
            f'const response = await api.request({{url:{url}, method:{json.dumps(method)}{options}}});\n'
            'return response.body?.item ?? null;'))
    return family


def javascript_canvas():
    family = canvas_family()
    inspect = 'const before=await api.download({css:"#export"});\n'
    guard = {"javascript": 'return await api.download({css:"#export"});'}
    for verb in family.operations:
        if verb == "read":
            code = inspect + 'return before.elements.find(x=>x.id===input.id) ?? null;'
        else:
            code = inspect + 'const after=JSON.parse(JSON.stringify(before));\nlet item=null;\n'
            if verb == "create":
                code += 'item={...input,id:await api.uuid()};after.elements.push(item);\n'
            elif verb == "update":
                code += 'item=after.elements.find(x=>x.id===input.id);Object.assign(item,input);\n'
            else:
                code += 'after.elements=after.elements.filter(x=>x.id!==input.id);\n'
            code += 'await api.upload({css:"#import"},after,{expected_document:before,verify_program:'
            code += json.dumps(guard) + '});return item;'
        family.operations[verb].program = Program(javascript=code)
    family.document_inspection = Program(javascript=guard["javascript"])
    return family
