import {
  Component,
  OnInit,
  Input,
  Renderer2,
  ViewChild,
  ElementRef,
  Pipe,
  PipeTransform
} from "@angular/core"
import { DatePipe } from '@angular/common'
import {
  FormControl,
  FormGroup,
  FormBuilder,
  FormsModule,
  ReactiveFormsModule,
  Validators
} from "@angular/forms"
import { Router } from "@angular/router"
import { Observable } from "rxjs/Observable"
import { TranslateService } from "@ngx-translate/core"
import { NgbModal, ModalDismissReasons } from "@ng-bootstrap/ng-bootstrap"
import { ToastrService } from 'ngx-toastr'
import { CommonService } from "@geonature_common/service/common.service"
// import { DynamicFormComponent } from "@geonature_common/form/dynamic-form/dynamic-form.component"
// import { DynamicFormService } from "@geonature_common/form/dynamic-form/dynamic-form.service"
import { Export, ExportService } from "../services/export.service"

@Component({
  selector: 'ng-pbar',
  template: `<div class="telechargement">{{message}}</div>
<p><ngb-progressbar [type]="type" [value]="progress$ | async" [striped]="true" [animated]="animated"></ngb-progressbar></p>`
})
export class NgPBar {
  progress$: Observable<number>
  @Input() message = 'Téléchargement en cours...'
  @Input() type ='info'
  @Input() animated = true

  constructor(private _exportService: ExportService) {}

  ngOnInit() {
    this.progress$ = this._exportService.downloadProgress
    this.progress$.subscribe(state => (state === 100) ? this.done() : null)
  }

  done() {
    this.message = 'Export téléchargé.'
    this.type = 'success'
    this.animated = false
  }
}

@Component({
  selector: "pnx-export-list",
  templateUrl: "export-list.component.html",
  styleUrls: ["./export-list.component.scss"],
  providers: []
})
export class ExportListComponent {
  exports$: Observable<Export[]>
  public modalForm : FormGroup
  public buttonDisabled: boolean = false
  public downloading: boolean = false
  public closeResult: string
  private _id_export: number

  @ViewChild('content') FormatSelector: ElementRef
  @ViewChild('contentApi') DatasetComposer: ElementRef

  constructor(
    private _exportService: ExportService,
    private _commonService: CommonService,
    private _translate: TranslateService,
    private _router: Router,
    private _fb: FormBuilder,
    // private _dynformService: DynamicFormService,
    private modalService: NgbModal,
    private toastr: ToastrService
  ) {}

  ngOnInit() {

    this.modalForm = this._fb.group({
      formatSelection:['', Validators.required],
    })

    this._exportService.getExports()
    this.exports$ = this._exportService.exports
  }

  get formatSelection() {
    return this.modalForm.get('formatSelection')
  }

  open(content) {
    this.modalService.open(content).result.then((result) => {
      this.closeResult = `Closed with: ${result}`
      console.debug('modalclosed result', this.closeResult)
      this.downloading = false
    }, (reason) => {
      this.closeResult = `Dismissed ${this.getDismissReason(reason)}`
      this.downloading = false
    })
  }

  private getDismissReason(reason: any): string {
    if (reason === ModalDismissReasons.ESC) {
      return 'by pressing ESC'
    } else if (reason === ModalDismissReasons.BACKDROP_CLICK) {
      return 'by clicking on a backdrop'
    } else {
      return `with: ${reason}`
    }
  }

  //Fonction qui bloque le boutton de validation tant que la licence n'est pas checkée
  follow() {
    this.buttonDisabled = !this.buttonDisabled
  }

  selectFormat(id_export?: number) {
    if (id_export) {
      this._id_export = id_export
      // TODO: populate FormatSelector from column feature types
      this.open(this.FormatSelector)
    }
  }

  download() {
    if (this.modalForm.valid && this._id_export) {
      this.downloading = !this.downloading
      const extension = this.formatSelection.value
      this.exports$.switchMap(
        (exports: Export[]) => exports.filter(
          (x: Export) => (x.id == this._id_export))
        ).take(1).subscribe(
        x => this._exportService.downloadExport(x, extension),
        e => {
          console.error(e.error)
          this.toastr.error(e.message, e.name, {timeOut: 0})
        }
      )
    }
  }
}